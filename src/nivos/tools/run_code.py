"""run_code tool — executes Python (pandas, matplotlib, data processing) or shell scripts inside Docker sandbox.

Outputs stdout, stderr, and generated artifact files (plots, CSVs, reports) directly to workspace/.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nivos.core.events import EventType, get_event_bus
from nivos.core.paths import get_workspace_dir
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.sandbox.docker_manager import (
    check_docker_availability,
    evaluate_command_security,
    execute_in_sandbox,
    get_persistent_auto_approve,
    get_sandbox_auto_approve,
)
from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120  # 2 minutes


def _log_run_code_activity(
    code_snippet: str,
    language: str,
    sandbox: str,
    status: str,
    created_files: List[str],
    error: Optional[str] = None,
) -> None:
    """Log run_code activity to event bus."""
    try:
        bus = get_event_bus()
        display_code = code_snippet.strip()
        if len(display_code) > 80:
            display_code = display_code[:77] + "..."

        file_note = f" (created: {', '.join(created_files)})" if created_files else ""
        bus.publish(
            EventType.TOOL_CALL_END,
            {
                "tool": "run_code",
                "summary": f"Executed {language} code in {sandbox}: {display_code}{file_note}",
                "status": status,
                "error": error,
            },
        )
    except Exception:
        pass


@ToolRegistry.register("run_code")
class RunCodeTool(BaseTool):
    """Executes Python code or shell scripts inside the isolated Docker sandbox."""

    tool_id = "run_code"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="run_code",
            description=(
                "Execute Python code (e.g., pandas analysis, data processing, matplotlib charts) "
                "or shell scripts inside the isolated Docker sandbox. Generated files and plots are saved "
                "directly to workspace/."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The complete Python script or code to execute.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Programming language ('python', 'bash', 'sh'). Defaults to 'python'.",
                        "default": "python",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 120).",
                        "default": 120,
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "User confirmation flag to execute.",
                        "default": False,
                    },
                },
                "required": ["code"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        code = str(params.get("code", "")).strip()
        language = str(params.get("language", "python")).strip().lower()
        timeout = int(params.get("timeout", DEFAULT_TIMEOUT))
        confirm = bool(params.get("confirm", False))
        context = params.get("context") or {}

        if not code:
            return ToolResult(
                tool_name="run_code",
                content="Error: 'code' parameter is required and cannot be empty.",
                success=False,
            )

        if language not in ("python", "py", "bash", "sh"):
            return ToolResult(
                tool_name="run_code",
                content=f"Error: Unsupported language '{language}'. Supported languages are python, bash, sh.",
                success=False,
            )

        # 1. Central policy verification
        ctx = context or {}
        origin_val = ctx.get("origin") or RequestOrigin.LOCAL_CHAT
        is_untrusted = bool(ctx.get("is_untrusted_content", False))

        allowed, reason = is_tool_allowed(
            "run_code",
            origin=origin_val,
            is_untrusted_content=is_untrusted,
            user_approved=confirm,
        )
        if not allowed:
            return ToolResult(
                tool_name="run_code",
                content=f"Security Policy Rejection: {reason}",
                success=False,
                error=reason,
            )

        # 2. Check Docker availability
        docker_avail, docker_msg = check_docker_availability()
        ws_dir = get_workspace_dir().resolve()
        ws_dir.mkdir(parents=True, exist_ok=True)

        # Record pre-execution workspace files to detect newly created artifacts
        before_files = set(ws_dir.glob("**/*"))

        # Check permissions & auto-approval
        # When language is python, command is 'python3 script.py'
        preview_cmd = f"python3 -c '{code[:50]}...'" if language == "python" else code[:60]
        cmd_allowed, sec_reason, auto_approved = evaluate_command_security(
            command=preview_cmd,
            origin=origin_val,
            is_sandbox=docker_avail,
        )
        if not cmd_allowed:
            return ToolResult(
                tool_name="run_code",
                content=f"Security Rejection: {sec_reason}",
                success=False,
                error=sec_reason,
            )

        # Requires approval unless auto-approved inside sandbox or explicitly confirmed
        if docker_avail:
            if not confirm and not auto_approved and not get_sandbox_auto_approve() and not get_persistent_auto_approve():
                # Ask approval
                return ToolResult(
                    tool_name="run_code",
                    content=(
                        f"Sandbox approval required: Run {language} code in Docker sandbox?\n"
                        f"Code:\n```\n{code[:300]}\n```\n"
                        f"Options: [Confirm / Deny]"
                    ),
                    success=False,
                    metadata={"needs_approval": True, "language": language, "sandbox": "docker"},
                )

            # DOCKER SANDBOX EXECUTION
            script_name = f"_nivos_code_{uuid.uuid4().hex[:8]}.py" if language == "python" else f"_nivos_code_{uuid.uuid4().hex[:8]}.sh"
            script_path = ws_dir / script_name

            try:
                script_path.write_text(code, encoding="utf-8")
                docker_cmd = f"python3 /workspace/{script_name}" if language == "python" else f"sh /workspace/{script_name}"

                res = execute_in_sandbox(command=docker_cmd, working_dir="", timeout=timeout)
                stdout = res.get("stdout", "")
                stderr = res.get("stderr", "")
                exit_code = res.get("exit_code", 0)
                success = bool(res.get("success", False))

                # Identify newly created files in workspace/
                after_files = set(ws_dir.glob("**/*"))
                new_files = sorted(
                    [
                        str(p.relative_to(ws_dir)).replace("\\", "/")
                        for p in (after_files - before_files)
                        if p.is_file() and p.name != script_name
                    ]
                )

                status = "success" if success else "failed"
                _log_run_code_activity(code, language, "docker", status, new_files, res.get("error"))

                files_note = f"\nFiles created in workspace/: {', '.join(new_files)}" if new_files else ""
                receipt = f"[Receipt: tool=run_code, sandbox=docker, status={status}, exit_code={exit_code}{files_note}]"
                output = f"{receipt}\nExit code: {exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}".strip()

                return ToolResult(
                    tool_name="run_code",
                    content=output,
                    success=success,
                    metadata={
                        "sandbox": "docker",
                        "language": language,
                        "exit_code": exit_code,
                        "created_files": new_files,
                    },
                )
            finally:
                if script_path.exists():
                    try:
                        script_path.unlink()
                    except Exception:
                        pass

        # 3. HOST WORKSPACE FALLBACK
        if not confirm:
            return ToolResult(
                tool_name="run_code",
                content=(
                    f"Host approval required: Docker is offline. Run code on host workspace?\n"
                    f"Language: {language}\n"
                    f"Code:\n```\n{code[:300]}\n```\n"
                    f"Options: [Confirm / Deny]"
                ),
                success=False,
                metadata={"needs_approval": True, "language": language, "sandbox": "host"},
            )

        # Host execution
        script_name = f"_nivos_host_{uuid.uuid4().hex[:8]}.py" if language == "python" else f"_nivos_host_{uuid.uuid4().hex[:8]}.bat"
        script_path = ws_dir / script_name

        try:
            script_path.write_text(code, encoding="utf-8")
            host_cmd = ["python", str(script_path)] if language == "python" else [str(script_path)]

            proc = subprocess.run(
                host_cmd,
                cwd=str(ws_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
            success = exit_code == 0

            after_files = set(ws_dir.glob("**/*"))
            new_files = sorted(
                [
                    str(p.relative_to(ws_dir)).replace("\\", "/")
                    for p in (after_files - before_files)
                    if p.is_file() and p.name != script_name
                ]
            )

            status = "success" if success else "failed"
            _log_run_code_activity(code, language, "host", status, new_files)

            files_note = f"\nFiles created in workspace/: {', '.join(new_files)}" if new_files else ""
            receipt = f"[Receipt: tool=run_code, sandbox=host, status={status}, exit_code={exit_code}{files_note}]"
            output = f"{receipt}\nExit code: {exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}".strip()

            return ToolResult(
                tool_name="run_code",
                content=output,
                success=success,
                metadata={
                    "sandbox": "host",
                    "language": language,
                    "exit_code": exit_code,
                    "created_files": new_files,
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_name="run_code",
                content=f"Host execution failed: {exc}",
                success=False,
                error=str(exc),
            )
        finally:
            if script_path.exists():
                try:
                    script_path.unlink()
                except Exception:
                    pass
