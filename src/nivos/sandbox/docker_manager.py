"""Docker sandbox manager for isolated shell execution.

Constraints & Security Guarantees:
1. One Ubuntu-based container with 2 GB memory limit, CPU limit, all capabilities dropped.
2. Non-root user (1000:1000), no privileged mode, no host network mode (bridge network).
3. Mounts ONLY workspace/; never the vault, config folder, cloud keys, or Slack tokens.
4. Never passes secrets or keys as environment variables.
5. pip, npm, apt installs allowed inside the container.
6. Container started on-demand and stopped after 300s of inactivity.
7. "Reset sandbox" stops and removes the container.
8. Approvals ON by default with full command shown.
9. Optional per-session auto-approve inside sandbox toggle (default OFF); NEVER applies to host.
10. Fallback to host workspace shell if Docker is unavailable, with in-app setup guidance.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.config import load_config
from nivos.core.events import EventType, get_event_bus
from nivos.core.paths import get_workspace_dir
from nivos.security.policy import RequestOrigin, is_tool_allowed

logger = logging.getLogger(__name__)

CONTAINER_NAME = "nivos-sandbox-container"
DEFAULT_IMAGE = "ubuntu:22.04"
MAX_OUTPUT_BYTES = 102_400  # 100 KB
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
IDLE_STOP_SECONDS = 300.0  # 5 minutes

# Session auto-approve state (in-memory, resets when server restarts)
_session_auto_approve_sandbox: bool = False
_last_activity_time: float = 0.0
_idle_timer: Optional[threading.Timer] = None
_lock = threading.Lock()


def get_sandbox_auto_approve() -> bool:
    """Return whether auto-approval inside the Docker sandbox is enabled for this session."""
    global _session_auto_approve_sandbox
    with _lock:
        return _session_auto_approve_sandbox


def set_sandbox_auto_approve(enabled: bool) -> bool:
    """Set the per-session auto-approve toggle for Docker sandbox execution."""
    global _session_auto_approve_sandbox
    with _lock:
        _session_auto_approve_sandbox = bool(enabled)
        logger.info("Sandbox auto-approve set to %s for this session", _session_auto_approve_sandbox)
        return _session_auto_approve_sandbox


def check_docker_availability() -> Tuple[bool, str]:
    """Check if the Docker CLI is installed and the daemon is reachable."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False, "Docker CLI not found on PATH. Install Docker Desktop with WSL2."

    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if proc.returncode == 0:
            return True, f"Docker daemon reachable (v{proc.stdout.strip()})"
        err_msg = proc.stderr.strip() or "Docker daemon not running"
        return False, f"Docker daemon unavailable: {err_msg}"
    except subprocess.TimeoutExpired:
        return False, "Docker daemon check timed out after 4 seconds."
    except Exception as exc:
        return False, f"Docker check failed: {exc}"


def is_container_running() -> bool:
    """Check if the nivos-sandbox-container is currently running."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0 and "true" in proc.stdout.lower()
    except Exception:
        return False


def ensure_container_running(image: Optional[str] = None) -> Tuple[bool, str]:
    """Ensure the Docker sandbox container is running with strict security flags."""
    global _last_activity_time
    with _lock:
        if is_container_running():
            _last_activity_time = time.time()
            _schedule_idle_stop()
            return True, f"Container '{CONTAINER_NAME}' is running."

        # Remove existing stopped container if present
        try:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER_NAME],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass

        cfg = load_config()
        configured_image = getattr(cfg.sandbox, "image", None) or image or DEFAULT_IMAGE
        ws_path = get_workspace_dir().resolve()
        ws_path.mkdir(parents=True, exist_ok=True)
        # Format workspace path for Docker on Windows
        ws_str = str(ws_path).replace("\\", "/")

        run_args = [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "--label",
            "nivos-sandbox=true",
            "--network",
            "bridge",
            "--security-opt=no-new-privileges:true",
            "--cap-drop=ALL",
            "--memory=2g",
            "--cpus=1.0",
            "--user",
            "1000:1000",
            "-v",
            f"{ws_str}:/workspace:rw",
            "-w",
            "/workspace",
            configured_image,
            "tail",
            "-f",
            "/dev/null",
        ]

        try:
            proc = subprocess.run(
                run_args,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if proc.returncode != 0:
                err = proc.stderr.strip() or proc.stdout.strip()
                logger.warning("Failed to start Docker sandbox container: %s", err)
                return False, f"Failed to start Docker container: {err}"

            _last_activity_time = time.time()
            _schedule_idle_stop()
            logger.info("Started Docker sandbox container '%s' with image '%s'", CONTAINER_NAME, configured_image)
            return True, f"Started container '{CONTAINER_NAME}'."
        except Exception as exc:
            logger.warning("Exception starting Docker sandbox container: %s", exc)
            return False, f"Exception starting Docker container: {exc}"


def stop_container() -> Tuple[bool, str]:
    """Stop the running Docker sandbox container."""
    try:
        proc = subprocess.run(
            ["docker", "stop", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            logger.info("Stopped idle Docker sandbox container '%s'", CONTAINER_NAME)
            return True, "Container stopped."
        return False, proc.stderr.strip()
    except Exception as exc:
        return False, str(exc)


def reset_sandbox() -> Tuple[bool, str]:
    """Force remove the Docker sandbox container to reset environment."""
    with _lock:
        try:
            proc = subprocess.run(
                ["docker", "rm", "-f", CONTAINER_NAME],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0:
                logger.info("Reset Docker sandbox container '%s'", CONTAINER_NAME)
                return True, "Sandbox container has been reset."
            return False, proc.stderr.strip() or "Container reset failed."
        except Exception as exc:
            logger.warning("Failed resetting sandbox: %s", exc)
            return False, f"Reset failed: {exc}"


def _schedule_idle_stop(delay: float = IDLE_STOP_SECONDS) -> None:
    """Schedule automatic stop of the container after idle inactivity."""
    global _idle_timer
    if _idle_timer:
        try:
            _idle_timer.cancel()
        except Exception:
            pass
    _idle_timer = threading.Timer(delay, _stop_if_idle)
    _idle_timer.daemon = True
    _idle_timer.start()


def _stop_if_idle() -> None:
    """Callback to stop container if idle for longer than IDLE_STOP_SECONDS."""
    with _lock:
        if not is_container_running():
            return
        idle_duration = time.time() - _last_activity_time
        if idle_duration >= IDLE_STOP_SECONDS:
            logger.info("Container '%s' idle for %.1fs >= %.1fs, stopping.", CONTAINER_NAME, idle_duration, IDLE_STOP_SECONDS)
            stop_container()


def execute_in_sandbox(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Execute a command inside the Docker sandbox container.
    
    Returns a dict with 'success', 'stdout', 'stderr', 'exit_code', 'error'.
    """
    global _last_activity_time
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))

    started, msg = ensure_container_running()
    if not started:
        return {
            "success": False,
            "stdout": "",
            "stderr": msg,
            "exit_code": 1,
            "error": msg,
        }

    # Working dir resolution inside container (/workspace/...)
    c_workdir = "/workspace"
    if working_dir:
        clean = working_dir.replace("\\", "/").strip().lstrip("/")
        if ".." in clean:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Security rejection: Path traversal '..' is forbidden.",
                "exit_code": 1,
                "error": "Path traversal forbidden",
            }
        if clean:
            c_workdir = f"/workspace/{clean}"

    exec_cmd = [
        "docker",
        "exec",
        "-w",
        c_workdir,
        CONTAINER_NAME,
        "sh",
        "-c",
        command,
    ]

    try:
        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        _last_activity_time = time.time()
        _schedule_idle_stop()

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Truncate output if exceeded limit
        if len(stdout) > MAX_OUTPUT_BYTES:
            stdout = stdout[:MAX_OUTPUT_BYTES] + f"\n... [Output truncated at {MAX_OUTPUT_BYTES} bytes]"
        if len(stderr) > MAX_OUTPUT_BYTES:
            stderr = stderr[:MAX_OUTPUT_BYTES] + f"\n... [Stderr truncated at {MAX_OUTPUT_BYTES} bytes]"

        return {
            "success": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "error": None if proc.returncode == 0 else f"Process exited with code {proc.returncode}",
        }
    except subprocess.TimeoutExpired:
        _last_activity_time = time.time()
        _schedule_idle_stop()
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds.",
            "exit_code": -1,
            "error": "Timeout",
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "error": str(exc),
        }


def get_sandbox_status_info() -> Dict[str, Any]:
    """Return complete status information for Settings UI."""
    docker_avail, msg = check_docker_availability()
    container_running = is_container_running() if docker_avail else False
    auto_approve = get_sandbox_auto_approve()

    guidance = ""
    if not docker_avail:
        guidance = (
            "Docker is not available. Shell execution will use the host workspace fallback.\n"
            "To enable container sandboxing, install Docker Desktop with WSL2.\n"
            "In %UserProfile%\\.wslconfig set:\n"
            "[wsl2]\n"
            "memory=4GB\n"
            "then run 'wsl --shutdown' once."
        )

    return {
        "docker_available": docker_avail,
        "docker_running": docker_avail,
        "container_running": container_running,
        "sandbox_backend": "docker" if docker_avail else "host",
        "auto_approve_session": auto_approve,
        "image": DEFAULT_IMAGE,
        "memory_limit": "2GB",
        "guidance": guidance,
    }

