"""open_app tool — safely launch Windows Start menu shortcuts and approved URL schemes.

Safety Rules:
1. Launch only Windows Start menu shortcuts (.lnk) and whitelisted URL schemes (ms-settings:, spotify:, mailto:).
2. Allowed arguments: an http/https URL or a file path strictly inside allowed_dirs.
3. Block dangerous binaries: cmd, powershell, pwsh, wscript, cscript, mshta, regedit, rundll32, and installers (*setup*, *install*, *.msi).
4. Never run as administrator.
5. First-launch approval prompt: Once / Always for this app / Deny.
   - Always saves the app to allowed_apps in config.toml.
6. Execution allowed only on LOCAL_CHAT typed by the user; forbidden from untrusted content or external channels.
7. Log every launch in Activity.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from nivos.core.config import load_config
from nivos.core.events import EventType, get_event_bus
from nivos.core.paths import get_workspace_dir
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.security.policy import RequestOrigin, is_path_safe_for_write, is_tool_allowed
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Disallowed dangerous executables and shells
_BLOCKED_BINARIES: Set[str] = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    "mshta",
    "mshta.exe",
    "regedit",
    "regedit.exe",
    "reg",
    "reg.exe",
    "rundll32",
    "rundll32.exe",
    "bash",
    "bash.exe",
    "sh",
    "sh.exe",
    "wsl",
    "wsl.exe",
    "conhost",
    "conhost.exe",
}

# Dangerous script extensions
_BLOCKED_EXTENSIONS: Set[str] = {
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".jse",
    ".wsf",
    ".wsh",
    ".hta",
    ".scr",
    ".reg",
    ".pif",
    ".com",
    ".exe",
}

# Whitelisted URL schemes
_ALLOWED_SCHEMES = ("ms-settings:", "spotify:", "mailto:")

# Standard built-in safe Windows utility apps
_SAFE_WINDOWS_APPS = {
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "mspaint": "mspaint.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
}


def _is_installer(name: str) -> bool:
    """Return True if application name or target indicates an installer."""
    n = name.lower()
    if n.endswith((".msi", ".msp")):
        return True
    return any(kw in n for kw in ("setup", "installer", "install", "update", "patch"))


def _scan_start_menu_shortcuts(app_name: str) -> Optional[Path]:
    """Scan user and system Start Menu directories for .lnk shortcuts matching app_name."""
    clean_name = app_name.strip().lower()
    search_dirs: List[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        p1 = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p1.is_dir():
            search_dirs.append(p1)

    progdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    if progdata:
        p2 = Path(progdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p2.is_dir():
            search_dirs.append(p2)

    # Search for matching .lnk file
    for sdir in search_dirs:
        for root, _, files in os.walk(sdir):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                stem = f[:-4].lower()
                if clean_name == stem or clean_name in stem:
                    return Path(root) / f

    return None


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    """Safely launch Windows Start menu shortcuts or allowed URL schemes with approval."""

    tool_id = "open_app"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_app",
            description=(
                "Launch an approved desktop application or URL scheme on Windows. "
                "Can open Start menu shortcuts (e.g. Notepad, Spotify, Obsidian) and "
                "URL schemes (ms-settings:, spotify:). Blocked programs (cmd, powershell, "
                "regedit, installers) are strictly rejected. Requires user approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Name of application or URL scheme to open (e.g. 'Notepad', 'Spotify', 'ms-settings:').",
                    },
                    "target": {
                        "type": "string",
                        "description": "Optional argument: an http/https URL or a file path inside allowed directories.",
                    },
                    "approval": {
                        "type": "string",
                        "enum": ["once", "always", "deny"],
                        "description": "User approval decision: 'once', 'always', or 'deny'.",
                    },
                },
                "required": ["app"],
            },
            category="system",
            requires_confirmation=True,
            timeout_seconds=30.0,
            required_capabilities=["system:launch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        app = str(params.get("app", "")).strip()
        target = params.get("target")
        approval = str(params.get("approval", "")).strip().lower()
        origin = params.get("origin", RequestOrigin.LOCAL_CHAT)
        is_untrusted_content = params.get("is_untrusted_content", False)

        if not app:
            return ToolResult(tool_name="open_app", content="Error: App name is required.", success=False)

        # 1. Central policy check (must be LOCAL_CHAT and not untrusted content)
        allowed, policy_reason = is_tool_allowed(
            "open_app",
            origin=origin,
            is_untrusted_content=is_untrusted_content,
        )
        if not allowed:
            _log_launch_activity(app, target, status="blocked_policy", error=policy_reason)
            return ToolResult(
                tool_name="open_app",
                content=f"Security rejection: {policy_reason}",
                success=False,
            )

        app_lower = app.lower()

        # 2. Block elevation / runas
        if "runas" in app_lower or "elevate" in app_lower:
            reason = "Security block: Running applications with elevated/administrator privileges is forbidden."
            _log_launch_activity(app, target, status="blocked_admin", error=reason)
            return ToolResult(tool_name="open_app", content=reason, success=False)

        # Block dangerous binaries and shells
        if app_lower in _BLOCKED_BINARIES or any(app_lower.endswith(b) for b in _BLOCKED_BINARIES):
            reason = f"Security block: Launching '{app}' is strictly forbidden for system security."
            _log_launch_activity(app, target, status="blocked_binary", error=reason)
            return ToolResult(tool_name="open_app", content=reason, success=False)

        # Block scripts and dangerous file extensions
        for ext in _BLOCKED_EXTENSIONS:
            if app_lower.endswith(ext):
                reason = f"Security block: Direct execution of '{ext}' files is forbidden."
                _log_launch_activity(app, target, status="blocked_extension", error=reason)
                return ToolResult(tool_name="open_app", content=reason, success=False)

        # Block installers
        if _is_installer(app):
            reason = f"Security block: Installer or setup program '{app}' cannot be launched."
            _log_launch_activity(app, target, status="blocked_installer", error=reason)
            return ToolResult(tool_name="open_app", content=reason, success=False)

        # 3. Validate optional target argument
        clean_target: Optional[str] = None
        if target:
            target_str = str(target).strip()
            if target_str.startswith(("http://", "https://")):
                clean_target = target_str
            else:
                # File path check: Must be inside workspace/ or allowed_dirs; reject path traversal
                if ".." in target_str:
                    reason = "Security block: Path traversal '..' is forbidden in target argument."
                    _log_launch_activity(app, target_str, status="blocked_path_traversal", error=reason)
                    return ToolResult(tool_name="open_app", content=reason, success=False)

                target_p = Path(target_str).expanduser()
                safe, reason = is_path_safe_for_write(str(target_p))
                # Also allow reading from workspace
                ws_dir = get_workspace_dir().resolve()
                try:
                    res_p = target_p.resolve()
                    if res_p == ws_dir or res_p.is_relative_to(ws_dir):
                        safe = True
                except Exception:
                    pass

                if not safe:
                    reason = f"Security block: Target path '{target_str}' must be within allowed workspace directories."
                    _log_launch_activity(app, target_str, status="blocked_target_path", error=reason)
                    return ToolResult(tool_name="open_app", content=reason, success=False)

                clean_target = str(res_p)

        # 4. Check approval & allowed_apps
        cfg = load_config()
        allowed_apps = [a.lower() for a in getattr(cfg.security, "allowed_apps", [])]

        is_always_allowed = app_lower in allowed_apps

        if approval == "deny":
            _log_launch_activity(app, clean_target, status="denied_user")
            return ToolResult(
                tool_name="open_app",
                content=f"Launch of '{app}' was denied by user.",
                success=False,
            )

        if not is_always_allowed and approval not in ("once", "always"):
            # Needs first-launch approval
            return ToolResult(
                tool_name="open_app",
                content=(
                    f"Approval required: Do you want to open '{app}'?\n"
                    f"Options: [Once / Always for this app / Deny]"
                ),
                success=False,
                metadata={
                    "needs_approval": True,
                    "app": app,
                    "target": clean_target,
                },
            )

        # If user approved with "always", persist to config.toml
        if approval == "always" and not is_always_allowed:
            try:
                from nivos.server.routes import _persist_config_section

                updated_apps = list(allowed_apps) + [app_lower]
                _persist_config_section("security", {"allowed_apps": updated_apps})
                logger.info("Added '%s' to allowed_apps in config.toml", app_lower)
            except Exception as exc:
                logger.debug("Failed to persist allowed_apps: %s", exc)

        # 5. Launch application or URL scheme
        launch_err = None
        receipt_status = "launched"

        try:
            # Scheme launch (ms-settings:, spotify:, mailto:)
            if app_lower.startswith(_ALLOWED_SCHEMES):
                if sys.platform == "win32" and hasattr(os, "startfile"):
                    os.startfile(app)
                else:
                    import webbrowser
                    webbrowser.open(app)
            else:
                # Start menu shortcut scanner
                shortcut_p = _scan_start_menu_shortcuts(app)
                if shortcut_p and shortcut_p.is_file():
                    if sys.platform == "win32" and hasattr(os, "startfile"):
                        if clean_target:
                            os.startfile(str(shortcut_p), arguments=clean_target)  # type: ignore[call-arg]
                        else:
                            os.startfile(str(shortcut_p))
                    else:
                        subprocess.Popen([str(shortcut_p)] + ([clean_target] if clean_target else []))
                elif app_lower in _SAFE_WINDOWS_APPS:
                    exe_name = _SAFE_WINDOWS_APPS[app_lower]
                    cmd_list = [exe_name]
                    if clean_target:
                        cmd_list.append(clean_target)
                    subprocess.Popen(cmd_list, shell=False)
                else:
                    # Application not found
                    return ToolResult(
                        tool_name="open_app",
                        content=f"Application '{app}' not found in Windows Start menu shortcuts.",
                        success=False,
                    )
        except Exception as exc:
            launch_err = str(exc)
            receipt_status = "failed"

        _log_launch_activity(app, clean_target, status=receipt_status, error=launch_err)

        if launch_err:
            return ToolResult(
                tool_name="open_app",
                content=f"Failed to launch '{app}': {launch_err}",
                success=False,
            )

        receipt = f"[Receipt: tool=open_app, app={app}, target={clean_target or 'none'}, status=launched]"
        return ToolResult(
            tool_name="open_app",
            content=f"{receipt}\nSuccessfully launched '{app}'.",
            success=True,
            metadata={"app": app, "target": clean_target, "status": "launched"},
        )


def _log_launch_activity(
    app: str,
    target: Optional[str] = None,
    status: str = "launched",
    error: Optional[str] = None,
) -> None:
    """Log launch attempt to the event bus and Activity log."""
    try:
        bus = get_event_bus()
        bus.publish(
            EventType.TOOL_EXECUTE,
            {
                "tool": "open_app",
                "app": app,
                "target": target,
                "status": status,
                "error": error,
            },
        )
    except Exception as exc:
        logger.debug("Failed logging open_app to activity: %s", exc)


__all__ = ["OpenAppTool", "_BLOCKED_BINARIES", "_ALLOWED_SCHEMES"]

