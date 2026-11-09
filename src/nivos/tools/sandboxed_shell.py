"""Sandboxed shell execution tool — isolated Docker container with host workspace fallback.

Phase 4 Security Constraints:
1. Docker Sandbox:
   - Uses an Ubuntu-based container (2 GB memory limit, CPU limit, all capabilities dropped).
   - Non-root user (1000:1000), no privileged mode, bridge network (no host network mode).
   - Mounts ONLY workspace/; never vault, config, keys, or tokens.
   - pip, npm, apt installs allowed inside container.
   - Container started on demand, stopped after idle (300s).
   - Per-session auto-approve toggle inside sandbox (default off).
2. Host Fallback:
   - Used only when Docker is not available.
   - Strictly limited to workspace/ directory.
   - Only 'git clone', 'pip install', and 'npm install' are permitted.
   - Per-session sandbox auto-approve NEVER applies to host commands.
   - In-app setup guidance displayed.
3. Central policy check:
   - Runs ONLY on turns typed in local chat (LOCAL_CHAT).
   - Rejects untrusted content and external channel requests.
4. Activity logging and tool receipts.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nivos.core.events import EventType, get_event_bus
from nivos.core.paths import get_workspace_dir
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.sandbox.docker_manager import (
    check_docker_availability,
    execute_in_sandbox,
    get_sandbox_auto_approve,
)
from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Allowed command prefixes for host fallback
_ALLOWED_COMMANDS = {
    ("git", "clone"),
    ("pip", "install"),
    ("npm", "install"),
    ("npm", "i"),
    ("npm", "add"),
}

# Forbidden shell chaining and redirection characters for host fallback
_DISALLOWED_SHELL_CHARS = re.compile(r"[;&|><`$]")

_DEFAULT_TIMEOUT = 300  # seconds


def _log_shell_activity(
    command: str,
    sandbox: str = "docker",
    status: str = "success",
    error: Optional[str] = None,
) -> None:
    """Log shell execution attempt to the event bus and Activity log."""
    try:
        bus = get_event_bus()
        bus.publish(
            EventType.TOOL_EXECUTE,
            {
                "tool": "sandboxed_shell",
                "command": command,
                "sandbox": sandbox,
                "status": status,
                "error": error,
            },
        )
    except Exception as exc:
        logger.debug("Failed logging sandboxed_shell to activity: %s", exc)


@ToolRegistry.register("sandboxed_shell")
class SandboxedShellTool(BaseTool):
    """Execute shell commands inside an isolated Docker sandbox with host workspace fallback."""

    tool_id = "sandboxed_shell"

    def __init__(
        self,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        workspace_dir: Optional[Path] = None,
    ) -> None:
        self._confirm_callback = confirm_callback
        self._workspace_dir = workspace_dir

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sandboxed_shell",
            description=(
                "Execute shell commands in an isolated sandbox. Uses a secure Docker "
                "container (Ubuntu, 2 GB RAM cap, strictly workspace/ mounted) if Docker is "
                "available. Falls back to host workspace execution (git/pip/npm only) if Docker "
                "is offline. Requires user approval by default."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Optional subdirectory inside workspace/ to run the command in. "
                            "Must be inside workspace/."
                        ),
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Explicit approval confirmation flag.",
                    },
                    "approval": {
                        "type": "string",
                        "enum": ["once", "always", "deny"],
                        "description": "Approval decision from user prompt.",
                    },
                },
                "required": ["command"],
            },
            category="system",
            requires_confirmation=True,
            timeout_seconds=300.0,
            required_capabilities=["code:execute"],
        )

    def _resolve_working_dir(self, requested_dir: Optional[str]) -> Path:
        """Resolve and validate the working directory within workspace/."""
        ws = (self._workspace_dir or get_workspace_dir()).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        if not requested_dir:
            return ws

        clean = requested_dir.replace("\\", "/").strip()
        if clean.startswith("/"):
            clean = clean.lstrip("/")

        target = (ws / clean).resolve()
        try:
            if not target.is_relative_to(ws):
                raise ValueError("Target directory is outside workspace/")
        except ValueError:
            raise ValueError(f"Target directory '{requested_dir}' is outside workspace/")

        target.mkdir(parents=True, exist_ok=True)
        return target

    def _validate_command(self, command: str) -> tuple[List[str], str | None]:
        """Validate command against whitelist and shell metacharacters for host execution."""
        if not command or not command.strip():
            return [], "No command provided."

        if _DISALLOWED_SHELL_CHARS.search(command):
            return [], (
                "Command contains disallowed characters (;, &, |, >, <, `, $). "
                "Command chaining and redirection are forbidden in host fallback shell."
            )

        try:
            tokens = shlex.split(command, posix=(os.name != "nt"))
        except Exception as exc:
            return [], f"Failed to parse command line: {exc}"

        if not tokens:
            return [], "Empty command."

        cmd = tokens[0].lower().replace(".exe", "")
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""

        if (cmd, subcmd) not in _ALLOWED_COMMANDS:
            allowed_fmt = ", ".join(f"'{c[0]} {c[1]}'" for c in sorted(_ALLOWED_COMMANDS))
            return [], (
                f"Command '{tokens[0]} {subcmd}'.strip() is not permitted on host fallback. "
                f"Host fallback shell is strictly limited to: {allowed_fmt}."
            )

        return tokens, None

    def execute(self, **params: Any) -> ToolResult:
        command = str(params.get("command", "")).strip()
        working_dir_param = params.get("working_dir")
        approval = str(params.get("approval", "")).strip().lower()
        confirm_passed = params.get("confirm") is True or approval in ("once", "always", "yes", "confirm")
        origin = params.get("origin", RequestOrigin.LOCAL_CHAT)
        is_untrusted_content = params.get("is_untrusted_content", False)

        if not command:
            return ToolResult(tool_name="sandboxed_shell", content="Error: No command provided.", success=False)

        # 1. Central policy check (LOCAL_CHAT only, untrusted content forbidden)
        allowed, policy_reason = is_tool_allowed(
            "sandboxed_shell",
            origin=origin,
            is_untrusted_content=is_untrusted_content,
        )
        if not allowed:
            _log_shell_activity(command, sandbox="unknown", status="blocked_policy", error=policy_reason)
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Security rejection: {policy_reason}",
                success=False,
            )

        if approval == "deny":
            _log_shell_activity(command, sandbox="unknown", status="denied_user")
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Command execution denied by user: '{command}'",
                success=False,
            )

        # 2. Check Docker availability
        docker_available, docker_reason = check_docker_availability()

        if docker_available:
            # DOCKER SANDBOX EXECUTION
            auto_approve = get_sandbox_auto_approve()
            if not confirm_passed and not auto_approve:
                return ToolResult(
                    tool_name="sandboxed_shell",
                    content=(
                        f"Sandbox approval required: Run command in Docker sandbox?\n"
                        f"Command: {command}\n"
                        f"Options: [Confirm / Deny]"
                    ),
                    success=False,
                    metadata={
                        "needs_approval": True,
                        "command": command,
                        "sandbox": "docker",
                        "working_dir": working_dir_param,
                    },
                )

            res = execute_in_sandbox(
                command=command,
                working_dir=working_dir_param,
                timeout=params.get("timeout", _DEFAULT_TIMEOUT),
            )

            status = "success" if res.get("success") else "failed"
            stdout = res.get("stdout", "")
            stderr = res.get("stderr", "")
            exit_code = res.get("exit_code", 0)
            bytes_out = len(stdout) + len(stderr)

            _log_shell_activity(
                command,
                sandbox="docker",
                status=status,
                error=res.get("error"),
            )

            receipt = f"[Receipt: tool=sandboxed_shell, sandbox=docker, status={status}, exit_code={exit_code}, bytes={bytes_out}]"
            content = f"{receipt}\nExit code: {exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}".strip()

            return ToolResult(
                tool_name="sandboxed_shell",
                content=content,
                success=res.get("success", False),
                metadata={
                    "sandbox": "docker",
                    "command": command,
                    "exit_code": exit_code,
                    "bytes": bytes_out,
                },
            )

        # 3. HOST WORKSPACE FALLBACK
        # Safety constraint: Per-session auto-approve NEVER applies to host commands!
        if not confirm_passed:
            return ToolResult(
                tool_name="sandboxed_shell",
                content=(
                    f"Host approval required: Run command on host workspace fallback?\n"
                    f"Notice: Docker is unavailable. Executing on host workspace.\n"
                    f"Command: {command}\n"
                    f"Options: [Confirm / Deny]"
                ),
                success=False,
                metadata={
                    "needs_approval": True,
                    "command": command,
                    "sandbox": "host",
                    "working_dir": working_dir_param,
                },
            )

        tokens, err = self._validate_command(command)
        if err:
            _log_shell_activity(command, sandbox="host", status="blocked_command", error=err)
            return ToolResult(tool_name="sandboxed_shell", content=err, success=False)

        try:
            cwd = self._resolve_working_dir(working_dir_param)
        except ValueError as exc:
            _log_shell_activity(command, sandbox="host", status="blocked_path", error=str(exc))
            return ToolResult(tool_name="sandboxed_shell", content=str(exc), success=False)

        # Check git clone destination
        if tokens[0].lower() == "git" and len(tokens) >= 3:
            for arg in tokens[2:]:
                if not arg.startswith("-") and not ("://" in arg or "@" in arg or arg.endswith(".git")):
                    dest_path = (cwd / arg).resolve()
                    ws = (self._workspace_dir or get_workspace_dir()).resolve()
                    try:
                        if not dest_path.is_relative_to(ws):
                            return ToolResult(
                                tool_name="sandboxed_shell",
                                content=f"Destination directory '{arg}' resolves outside workspace/.",
                                success=False,
                            )
                    except ValueError:
                        return ToolResult(
                            tool_name="sandboxed_shell",
                            content=f"Destination directory '{arg}' resolves outside workspace/.",
                            success=False,
                        )

        try:
            proc = subprocess.run(
                tokens,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=_DEFAULT_TIMEOUT,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            bytes_out = len(stdout) + len(stderr)
            status = "success" if proc.returncode == 0 else "failed"

            _log_shell_activity(command, sandbox="host", status=status)

            notice = "[Notice: Docker unavailable. Executed on host workspace fallback. See Settings for Docker setup guidance.]"
            receipt = f"[Receipt: tool=sandboxed_shell, sandbox=host, status={status}, exit_code={proc.returncode}, bytes={bytes_out}]"
            content = f"{notice}\n{receipt}\nExit code: {proc.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}".strip()

            return ToolResult(
                tool_name="sandboxed_shell",
                content=content,
                success=(proc.returncode == 0),
                metadata={
                    "sandbox": "host",
                    "command": command,
                    "working_dir": str(cwd),
                    "exit_code": proc.returncode,
                    "bytes": bytes_out,
                },
            )
        except subprocess.TimeoutExpired:
            _log_shell_activity(command, sandbox="host", status="timeout")
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Command timed out after {_DEFAULT_TIMEOUT} seconds on host.",
                success=False,
            )
        except Exception as exc:
            _log_shell_activity(command, sandbox="host", status="failed", error=str(exc))
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Host execution failed: {exc}",
                success=False,
            )
