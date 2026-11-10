"""install_app tool — safely install applications via winget using exact package IDs.

Safety Invariants:
1. Exact package IDs only (e.g. 'Spotify.Spotify', 'Git.Git', 'Notion.Notion').
2. Queries and shows Name, Publisher, Version, and Source before asking for user approval.
3. User approval required once per package before execution.
4. Never runs silently (no --silent or --quiet flag) so user retains full visibility and control.
5. Never executes raw downloaded installers directly.
6. Execution permitted strictly from LOCAL_CHAT; forbidden from external channels, scheduled tasks, or untrusted content.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.events import EventType, get_event_bus
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Valid exact winget package ID pattern (e.g. Publisher.PackageName)
_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+$")


def _query_winget_package_info(package_id: str) -> Optional[Dict[str, str]]:
    """Query winget show for package details (Name, Publisher, Version, Source)."""
    if not shutil.which("winget"):
        return None

    try:
        proc = subprocess.run(
            [
                "winget",
                "show",
                "--id",
                package_id,
                "--exact",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return None

        stdout = proc.stdout
        info: Dict[str, str] = {
            "package_id": package_id,
            "name": package_id,
            "publisher": "Unknown",
            "version": "Latest",
            "source": "winget",
        }

        # Parse key fields from winget show output
        for line in stdout.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            if line_s.startswith("Found ") and ":" in line_s:
                # e.g. Found Git [Git.Git] Version 2.44.0
                m = re.search(r"Found (.+?) \[(.+?)\] Version (.+)", line_s)
                if m:
                    info["name"] = m.group(1).strip()
                    info["version"] = m.group(3).strip()
            elif line_s.startswith("Publisher:") or line_s.startswith("Author:"):
                info["publisher"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("Version:"):
                info["version"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("Source:"):
                info["source"] = line_s.split(":", 1)[1].strip()

        return info
    except Exception as exc:
        logger.debug("Failed querying winget package info for '%s': %s", package_id, exc)
        return None


def _log_install_activity(
    package_id: str,
    status: str = "installed",
    error: Optional[str] = None,
) -> None:
    """Log install_app activity to event bus."""
    try:
        bus = get_event_bus()
        bus.publish(
            EventType.TOOL_EXECUTE,
            {
                "tool": "install_app",
                "package_id": package_id,
                "status": status,
                "error": error,
            },
        )
    except Exception:
        pass


@ToolRegistry.register("install_app")
class InstallAppTool(BaseTool):
    """Safely installs desktop applications using Windows Package Manager (winget) exact package IDs."""

    tool_id = "install_app"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="install_app",
            description=(
                "Install a Windows desktop application using its exact winget package ID "
                "(e.g. 'Spotify.Spotify', 'Git.Git', 'Notion.Notion'). Shows package Name, "
                "Publisher, Version, and Source, and requires explicit user confirmation before installing. "
                "Never runs downloaded raw installers directly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "package_id": {
                        "type": "string",
                        "description": "Exact package ID from winget (e.g. 'Spotify.Spotify', 'Git.Git').",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "User confirmation flag to execute installation.",
                        "default": False,
                    },
                },
                "required": ["package_id"],
            },
            category="system",
            requires_confirmation=True,
            timeout_seconds=300.0,
            required_capabilities=["system:install"],
        )

    def execute(self, **params: Any) -> ToolResult:
        pkg_id = str(params.get("package_id", "")).strip()
        confirm = bool(params.get("confirm", False))
        context = params.get("context") or {}
        origin = context.get("origin") or params.get("origin") or RequestOrigin.LOCAL_CHAT
        is_untrusted = bool(context.get("is_untrusted_content") or params.get("is_untrusted_content", False))

        if not pkg_id:
            return ToolResult(
                tool_name="install_app",
                content="Error: 'package_id' is required.",
                success=False,
            )

        # 1. Central policy check (dangerous tool: must be LOCAL_CHAT and user confirmed)
        allowed, policy_reason = is_tool_allowed(
            "install_app",
            origin=origin,
            is_untrusted_content=is_untrusted,
            user_approved=confirm,
        )
        if not allowed:
            _log_install_activity(pkg_id, status="blocked_policy", error=policy_reason)
            return ToolResult(
                tool_name="install_app",
                content=f"Security Policy Rejection: {policy_reason}",
                success=False,
            )

        # 2. Validate exact package ID format
        if not _PACKAGE_ID_RE.match(pkg_id):
            return ToolResult(
                tool_name="install_app",
                content=(
                    f"Error: Invalid package ID '{pkg_id}'. An exact package ID in the format "
                    f"'Publisher.PackageName' (e.g. 'Spotify.Spotify', 'Git.Git') is required."
                ),
                success=False,
            )

        # 3. Check winget availability
        if not shutil.which("winget"):
            return ToolResult(
                tool_name="install_app",
                content="Error: Windows Package Manager (winget) is not installed or not on system PATH.",
                success=False,
            )

        # 4. Query package details from winget
        pkg_info = _query_winget_package_info(pkg_id)
        if not pkg_info:
            return ToolResult(
                tool_name="install_app",
                content=f"Error: Package '{pkg_id}' was not found in winget sources.",
                success=False,
            )

        # 5. Require confirmation if not confirmed or if turn contains untrusted content
        if not confirm or is_untrusted:
            prompt = (
                f"Approval required: Do you want to install this application?\n\n"
                f"• Name: {pkg_info['name']}\n"
                f"• Publisher: {pkg_info['publisher']}\n"
                f"• Version: {pkg_info['version']}\n"
                f"• Source: {pkg_info['source']}\n"
                f"• Package ID: {pkg_id}\n\n"
                f"Options: [Confirm / Deny]"
            )
            return ToolResult(
                tool_name="install_app",
                content=prompt,
                success=False,
                metadata={
                    "needs_approval": True,
                    "package_id": pkg_id,
                    "name": pkg_info["name"],
                    "publisher": pkg_info["publisher"],
                    "version": pkg_info["version"],
                    "source": pkg_info["source"],
                },
            )

        # 6. Execute installation (never silent, never run downloaded raw binaries)
        install_cmd = [
            "winget",
            "install",
            "--id",
            pkg_id,
            "--exact",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]

        try:
            proc = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            exit_code = proc.returncode
            success = exit_code == 0

            status = "installed" if success else "failed"
            err_msg = stderr if not success else None
            _log_install_activity(pkg_id, status=status, error=err_msg)

            receipt = f"[Receipt: tool=install_app, package_id={pkg_id}, status={status}, exit_code={exit_code}]"
            content = f"{receipt}\n{stdout}"
            if stderr:
                content += f"\nStderr:\n{stderr}"

            return ToolResult(
                tool_name="install_app",
                content=content,
                success=success,
                metadata={
                    "package_id": pkg_id,
                    "status": status,
                    "exit_code": exit_code,
                },
            )
        except subprocess.TimeoutExpired:
            _log_install_activity(pkg_id, status="timeout", error="Installation timed out after 300s.")
            return ToolResult(
                tool_name="install_app",
                content=f"Error: Installation of '{pkg_id}' timed out.",
                success=False,
            )
        except Exception as exc:
            _log_install_activity(pkg_id, status="error", error=str(exc))
            return ToolResult(
                tool_name="install_app",
                content=f"Error installing '{pkg_id}': {exc}",
                success=False,
            )
