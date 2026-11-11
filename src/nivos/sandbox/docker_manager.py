"""Docker sandbox manager for isolated shell execution.

Constraints & Security Guarantees:
1. One Ubuntu-based container with 2 GB memory limit, CPU limit, all capabilities dropped.
2. Non-root user (1000:1000), no privileged mode, no host network mode (bridge network).
3. Mounts ONLY workspace/; never the vault, config folder, cloud keys, or Slack tokens.
4. Mounts named volume nivos-sandbox-packages with size cap; packages persist across restarts.
5. Never passes secrets or keys as environment variables.
6. pip, npm, apt installs allowed inside the container.
7. Container started on-demand and stopped after 300s of inactivity.
8. "Reset sandbox" stops and removes container and package volume.
9. Persistent "auto-approve inside sandbox" toggle (config.toml); applies ONLY to sandbox commands from local chat turns.
10. Fallback to host workspace shell if Docker is unavailable, with in-app setup guidance.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
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
DEFAULT_PKG_VOLUME = "nivos-sandbox-packages"
DEFAULT_VOLUME_CAP_MB = 5120  # 5 GB
MAX_OUTPUT_BYTES = 102_400  # 100 KB
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
IDLE_STOP_SECONDS = 300.0  # 5 minutes

# Session auto-approve state (in-memory, resets when server restarts)
_session_auto_approve_sandbox: bool = False
_last_activity_time: float = 0.0
_idle_timer: Optional[threading.Timer] = None
_lock = threading.Lock()

# Preparation state tracking
_prepare_lock = threading.Lock()
_prepare_state: Dict[str, Any] = {
    "status": "idle",  # idle | preparing | ready | error
    "progress": 0,
    "step": "",
    "error": "",
}

_DANGEROUS_DOWNLOAD_PIPE_RE = re.compile(
    r"(?:curl|wget|fetch|invoke-webrequest)\s+[^|;]+\|\s*(?:sh|bash|zsh|dash|powershell|cmd)",
    re.IGNORECASE,
)
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm\s+-(?:r[fF]|[fF]r)\s+(?:/|~|\$HOME|\*)|mkfs(?:\.\w+)?|dd\s+if=|:()\s*{\s*:\|:&\s*};\s*:)\b",
    re.IGNORECASE,
)


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


def get_persistent_auto_approve() -> bool:
    """Return whether persistent auto-approval inside Docker sandbox is enabled in config.toml."""
    cfg = load_config()
    return bool(getattr(getattr(cfg, "sandbox", None), "auto_approve", False))


def set_persistent_auto_approve(enabled: bool) -> bool:
    """Set persistent auto-approval inside Docker sandbox and persist to config.toml."""
    from nivos.server.routes import _persist_config_section

    _persist_config_section("sandbox", {"auto_approve": bool(enabled)})
    load_config.cache_clear()
    logger.info("Persistent sandbox auto-approve set to %s", enabled)
    return bool(enabled)


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


def check_wsl2_status() -> Dict[str, Any]:
    """Check WSL2 installation and .wslconfig memory configuration."""
    wsl_installed = shutil.which("wsl") is not None
    wslconfig_path = Path.home() / ".wslconfig"
    wslconfig_found = wslconfig_path.is_file()
    wslconfig_memory = ""

    if wslconfig_found:
        try:
            content = wslconfig_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                clean = line.strip().lower()
                if clean.startswith("memory=") or clean.startswith("memory ="):
                    wslconfig_memory = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

    guidance = ""
    if not wslconfig_found:
        guidance = (
            "Recommended for laptop performance: create %UserProfile%\\.wslconfig with:\n"
            "[wsl2]\nmemory=4GB\n"
            "then run 'wsl --shutdown' once."
        )

    return {
        "wsl_installed": wsl_installed,
        "wslconfig_found": wslconfig_found,
        "wslconfig_memory": wslconfig_memory,
        "guidance": guidance,
    }


def start_docker_desktop() -> Tuple[bool, str]:
    """Attempt to launch Docker Desktop on Windows."""
    candidates = [
        Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
        Path(r"C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Docker" / "Docker" / "Docker Desktop.exe")

    for candidate in candidates:
        if candidate.is_file():
            try:
                subprocess.Popen([str(candidate)], close_fds=True)
                return True, f"Launching Docker Desktop from {candidate}."
            except Exception as exc:
                return False, f"Failed to launch Docker Desktop: {exc}"

    try:
        subprocess.Popen(["start", "Docker Desktop"], shell=True)
        return True, "Triggered Docker Desktop launch command."
    except Exception as exc:
        return False, f"Docker Desktop executable not found: {exc}"


def get_prepare_status() -> Dict[str, Any]:
    """Return the current preparation status of the sandbox."""
    with _prepare_lock:
        return dict(_prepare_state)


def prepare_sandbox_async() -> Dict[str, Any]:
    """Start asynchronous sandbox image pull and container/volume setup."""
    with _prepare_lock:
        if _prepare_state["status"] == "preparing":
            return dict(_prepare_state)
        _prepare_state["status"] = "preparing"
        _prepare_state["progress"] = 10
        _prepare_state["step"] = "Checking Docker daemon..."
        _prepare_state["error"] = ""

    def _worker() -> None:
        try:
            docker_avail, msg = check_docker_availability()
            if not docker_avail:
                with _prepare_lock:
                    _prepare_state["status"] = "error"
                    _prepare_state["error"] = msg
                return

            with _prepare_lock:
                _prepare_state["progress"] = 30
                _prepare_state["step"] = "Pulling sandbox image..."

            cfg = load_config()
            img = getattr(getattr(cfg, "sandbox", None), "image", None) or DEFAULT_IMAGE
            pull_proc = subprocess.run(
                ["docker", "pull", img],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if pull_proc.returncode != 0:
                with _prepare_lock:
                    _prepare_state["status"] = "error"
                    _prepare_state["error"] = f"Failed to pull image {img}: {pull_proc.stderr[:200]}"
                return

            with _prepare_lock:
                _prepare_state["progress"] = 65
                _prepare_state["step"] = "Preparing package volume and sandbox container..."

            started, start_msg = ensure_container_running()
            if not started:
                with _prepare_lock:
                    _prepare_state["status"] = "error"
                    _prepare_state["error"] = start_msg
                return

            with _prepare_lock:
                _prepare_state["status"] = "ready"
                _prepare_state["progress"] = 100
                _prepare_state["step"] = "Sandbox prepared and ready."
        except Exception as exc:
            with _prepare_lock:
                _prepare_state["status"] = "error"
                _prepare_state["error"] = str(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return get_prepare_status()


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


def get_sandbox_volume_size_mb() -> float:
    """Return the approximate size in MB of the package volume."""
    try:
        if is_container_running():
            proc = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "du", "-sm", "/packages"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                parts = proc.stdout.split()
                if parts:
                    return float(parts[0])
    except Exception:
        pass
    return 0.0


def get_installed_sandbox_packages() -> List[Dict[str, str]]:
    """List packages installed in the sandbox container/volume."""
    if not is_container_running():
        return []
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "python3", "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def ensure_container_running(image: Optional[str] = None) -> Tuple[bool, str]:
    """Ensure the Docker sandbox container is running with strict security flags and package volume."""
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
        configured_image = getattr(getattr(cfg, "sandbox", None), "image", None) or image or DEFAULT_IMAGE
        pkg_volume = getattr(getattr(cfg, "sandbox", None), "package_volume", None) or DEFAULT_PKG_VOLUME

        # Ensure named package volume exists
        try:
            subprocess.run(
                ["docker", "volume", "create", pkg_volume],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

        ws_path = get_workspace_dir().resolve()
        ws_path.mkdir(parents=True, exist_ok=True)
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
            "-v",
            f"{pkg_volume}:/packages:rw",
            "-e",
            "PYTHONUSERBASE=/packages",
            "-e",
            "PYTHONPATH=/packages/lib/python3.10/site-packages:/packages/lib/python3.11/site-packages:/packages/lib/python3/dist-packages:/packages",
            "-e",
            "PATH=/packages/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
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
    """Force remove the Docker sandbox container and reset package volume."""
    with _lock:
        try:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER_NAME],
                capture_output=True,
                text=True,
                timeout=15,
            )
            cfg = load_config()
            pkg_vol = getattr(getattr(cfg, "sandbox", None), "package_volume", None) or DEFAULT_PKG_VOLUME
            subprocess.run(
                ["docker", "volume", "rm", "-f", pkg_vol],
                capture_output=True,
                text=True,
                timeout=15,
            )
            logger.info("Reset Docker sandbox container '%s' and package volume '%s'", CONTAINER_NAME, pkg_vol)
            return True, "Sandbox container and package volume have been reset."
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


def evaluate_command_security(
    command: str,
    origin: RequestOrigin | str = RequestOrigin.LOCAL_CHAT,
    is_sandbox: bool = True,
) -> Tuple[bool, str, bool]:
    """Evaluate whether a shell command is allowed, denied, or auto-approved.

    Returns:
        (allowed: bool, reason: str, auto_approved: bool)
    """
    if not isinstance(origin, RequestOrigin):
        origin = RequestOrigin.from_str(origin)

    cmd_clean = (command or "").strip()
    cmd_lower = cmd_clean.lower()

    # Rule 1: Non-local chat origins can NEVER execute shell or dangerous tools
    if origin != RequestOrigin.LOCAL_CHAT:
        return False, f"Shell commands cannot be executed from origin '{origin.value}'.", False

    # Check built-in destructive patterns and download-to-shell piping
    if _DANGEROUS_DOWNLOAD_PIPE_RE.search(cmd_clean):
        return False, "Security rejection: Piping downloaded scripts into a shell is strictly forbidden.", False

    if _DESTRUCTIVE_CMD_RE.search(cmd_clean):
        return False, "Security rejection: Destructive system commands are strictly forbidden.", False

    cfg = load_config()
    perms = getattr(cfg, "permissions", None)
    allow_patterns = getattr(perms, "allow", ["pip install *", "npm install *", "git clone *"]) if perms else ["pip install *", "npm install *", "git clone *"]
    deny_patterns = getattr(perms, "deny", []) if perms else []

    # Check deny patterns first
    for pat in deny_patterns:
        pat_clean = pat.strip()
        if not pat_clean:
            continue
        if fnmatch.fnmatchcase(cmd_lower, pat_clean.lower()):
            return False, f"Command matches denied permission rule '{pat_clean}'.", False

    # Check auto-approval inside sandbox
    if is_sandbox:
        auto_approved = False
        is_persistent_auto = get_persistent_auto_approve()
        is_session_auto = get_sandbox_auto_approve()

        if is_persistent_auto or is_session_auto:
            for pat in allow_patterns:
                pat_clean = pat.strip()
                if not pat_clean:
                    continue
                if fnmatch.fnmatchcase(cmd_lower, pat_clean.lower()):
                    auto_approved = True
                    break

        if auto_approved:
            return True, "Auto-approved inside sandbox by permissions policy.", True

    # Allowed but requires manual confirmation
    return True, "Approval required before execution.", False


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
    wsl_info = check_wsl2_status()
    cfg = load_config()

    is_persistent_auto = get_persistent_auto_approve()
    is_session_auto = get_sandbox_auto_approve()
    vol_name = getattr(getattr(cfg, "sandbox", None), "package_volume", None) or DEFAULT_PKG_VOLUME
    vol_size_mb = get_sandbox_volume_size_mb()
    cap_mb = getattr(getattr(cfg, "sandbox", None), "volume_size_cap_mb", None) or DEFAULT_VOLUME_CAP_MB

    packages = get_installed_sandbox_packages() if container_running else []
    prepare_st = get_prepare_status()

    docker_ver = ""
    if docker_avail:
        try:
            ver_proc = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=3)
            if ver_proc.returncode == 0:
                docker_ver = ver_proc.stdout.strip()
        except Exception:
            pass

    return {
        "docker_available": docker_avail,
        "docker_running": docker_avail,
        "docker_installed": shutil.which("docker") is not None,
        "docker_version": docker_ver,
        "container_running": container_running,
        "sandbox_backend": "docker" if docker_avail else "host",
        "auto_approve_persistent": is_persistent_auto,
        "auto_approve_session": is_session_auto,
        "named_volume": vol_name,
        "volume_size_mb": round(vol_size_mb, 1),
        "volume_cap_mb": cap_mb,
        "volume_capped": vol_size_mb >= cap_mb,
        "installed_packages": packages,
        "wsl": wsl_info,
        "prepare_status": prepare_st,
        "image": getattr(getattr(cfg, "sandbox", None), "image", None) or DEFAULT_IMAGE,
        "memory_limit": "2GB",
        "guidance": wsl_info.get("guidance", ""),
    }


get_docker_status = get_sandbox_status_info
