"""Sandboxed shell execution tool — limited to workspace/ for git clone, pip install, npm install."""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nivos.core.paths import get_workspace_dir
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Allowed command prefixes (first two tokens)
_ALLOWED_COMMANDS = {
    ("git", "clone"),
    ("pip", "install"),
    ("npm", "install"),
    ("npm", "i"),
    ("npm", "add"),
}

# Forbidden shell chaining and redirection characters
_DISALLOWED_SHELL_CHARS = re.compile(r"[;&|><`$]")

_DEFAULT_TIMEOUT = 300  # seconds


@ToolRegistry.register("sandboxed_shell")
class SandboxedShellTool(BaseTool):
    """Execute shell commands strictly sandboxed within workspace/."""

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
                "Execute sandboxed shell commands strictly limited to workspace/. "
                "Only 'git clone', 'pip install', and 'npm install' are permitted. "
                "Requires user approval before execution."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to execute (strictly git clone, pip install, or npm install).",
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
        """Validate command against whitelist and shell metacharacters."""
        if not command or not command.strip():
            return [], "No command provided."

        # Check for command chaining / injection operators
        if _DISALLOWED_SHELL_CHARS.search(command):
            return [], (
                "Command contains disallowed characters (;, &, |, >, <, `, $). "
                "Command chaining and redirection are forbidden in the sandboxed shell."
            )

        try:
            tokens = shlex.split(command, posix=(os.name != "nt"))
        except Exception as exc:
            return [], f"Failed to parse command line: {exc}"

        if not tokens:
            return [], "Empty command."

        # Normalize cmd and subcmd
        cmd = tokens[0].lower().replace(".exe", "")
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""

        if (cmd, subcmd) not in _ALLOWED_COMMANDS:
            allowed_fmt = ", ".join(f"'{c[0]} {c[1]}'" for c in sorted(_ALLOWED_COMMANDS))
            return [], (
                f"Command '{tokens[0]} {subcmd}'.strip() is not permitted. "
                f"The sandboxed shell tool is strictly limited to: {allowed_fmt}."
            )

        return tokens, None

    def execute(self, **params: Any) -> ToolResult:
        command = params.get("command", "")
        tokens, err = self._validate_command(command)
        if err:
            return ToolResult(tool_name="sandboxed_shell", content=err, success=False)

        # Validate working directory
        working_dir_param = params.get("working_dir")
        try:
            cwd = self._resolve_working_dir(working_dir_param)
        except ValueError as exc:
            return ToolResult(tool_name="sandboxed_shell", content=str(exc), success=False)

        # Check destination if git clone
        if tokens[0].lower() == "git" and len(tokens) >= 3:
            # git clone <url> [target_dir]
            for arg in tokens[2:]:
                if not arg.startswith("-") and not ("://" in arg or "@" in arg or arg.endswith(".git")):
                    # Potential destination directory
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

        # Approve / Deny verification
        confirm_passed = params.get("confirm") is True
        if not confirm_passed:
            if self._confirm_callback is not None:
                prompt = (
                    f"Sandboxed shell request:\n"
                    f"Command: {command}\n"
                    f"Directory: {cwd}\n"
                    f"Approve execution? [y/N]: "
                )
                if not self._confirm_callback(prompt):
                    return ToolResult(
                        tool_name="sandboxed_shell",
                        content=f"Command execution denied by user: '{command}'",
                        success=False,
                    )
            else:
                # Check ApprovalStore
                try:
                    from nivos.tools.approval_store import (
                        ApprovalStore,
                        DECISION_ALWAYS_DENY,
                        TIER_HIGH,
                    )

                    store = ApprovalStore()
                    perm_key = f"sandboxed_shell:{tokens[0]}_{tokens[1]}"
                    if store.check_permission(perm_key) == DECISION_ALWAYS_DENY:
                        return ToolResult(
                            tool_name="sandboxed_shell",
                            content="Command execution denied by saved permission rule.",
                            success=False,
                        )
                except Exception:
                    pass

        # Execute command in sandbox
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
            output = f"Exit code: {proc.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
            return ToolResult(
                tool_name="sandboxed_shell",
                content=output,
                success=(proc.returncode == 0),
                metadata={
                    "returncode": proc.returncode,
                    "command": command,
                    "working_dir": str(cwd),
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Command timed out after {_DEFAULT_TIMEOUT} seconds.",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="sandboxed_shell",
                content=f"Execution failed: {exc}",
                success=False,
            )

