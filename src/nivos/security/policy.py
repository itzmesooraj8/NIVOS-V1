"""Central Origin and Untrusted-Content Security Policy for Nivos.

Enforces execution boundaries across all request origins (local chat, scheduled tasks,
Slack, channels) and content trust tiers (trusted user prompts vs. untrusted web/notes/files/Slack content).
"""

from __future__ import annotations

import enum
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class RequestOrigin(str, enum.Enum):
    """Origin of a request entering the Nivos system."""

    LOCAL_CHAT = "local_chat"
    SCHEDULED_TASK = "scheduled_task"
    SLACK = "slack"
    CHANNEL = "channel"
    AUTOMATION = "automation"
    OTHER = "other"

    @classmethod
    def from_str(cls, val: Any) -> "RequestOrigin":
        if isinstance(val, cls):
            return val
        if not val:
            return cls.LOCAL_CHAT
        raw = getattr(val, "value", str(val))
        val_clean = str(raw).strip().lower()
        for member in cls:
            if member.value == val_clean or member.name.lower() == val_clean:
                return member
        return cls.OTHER


# Tools that can execute code, launch system apps, or perform dangerous side effects
DANGEROUS_TOOLS: Set[str] = {
    "sandboxed_shell",
    "shell_exec",
    "docker_shell",
    "code_interpreter_docker",
    "open_app",
    "install_app",
    "self_improve",
    "run_code",
}

# Tools explicitly permitted for read-only / channel / scheduled origins
SAFE_READONLY_TOOLS: Set[str] = {
    "web_search",
    "get_weather",
    "search_vault",
    "calculator",
    "time",
    "get_current_time",
}

# Paths and file patterns that can NEVER be written to by any agent or tool
PROTECTED_WRITE_PATTERNS = [
    "me.md",
    "nivos-memory/me.md",
    "safety_rules.md",
    ".env",
]


def _get_canonical_vault_inbox() -> Optional[Path]:
    """Resolve the canonical Nivos-Inbox directory inside the configured Obsidian vault."""
    try:
        from nivos.core.config import load_config

        cfg = load_config()
        obs_cfg = getattr(cfg, "obsidian", None)
        vp = getattr(obs_cfg, "vault_path", "") if obs_cfg else ""
        if not vp:
            vp = r"C:\Nivos\NIVOS-vault"
        vault_p = Path(vp).expanduser().resolve()
        return (vault_p / "Nivos-Inbox").resolve()
    except Exception:
        return None


def _validate_canonical_path(
    path_str: str | Path,
    operation: str = "write",
    allowed_dirs: Optional[list[str]] = None,
) -> Tuple[bool, str]:
    """Canonical path validator preventing traversal, substring spoofing, and escaping symlinks."""
    if not path_str or (isinstance(path_str, str) and not path_str.strip()):
        return False, "Target path is missing or invalid."

    raw_path_str = str(path_str).strip()
    norm_path = raw_path_str.replace("\\", "/").lower()

    # Reject protected patterns for mutating operations (write, patch, delete)
    if operation in ("write", "patch", "delete"):
        for protected in PROTECTED_WRITE_PATTERNS:
            if norm_path == protected or norm_path.endswith(f"/{protected}") or f"/{protected}/" in norm_path:
                return False, f"{operation.capitalize()} operation on protected file '{protected}' is strictly forbidden."

    from nivos.security.file_policy import is_sensitive_file

    target_p = Path(raw_path_str).expanduser()
    try:
        resolved = target_p.resolve()
    except Exception as exc:
        return False, f"Could not resolve canonical path '{path_str}': {exc}"

    # Check sensitivity
    if is_sensitive_file(target_p) or is_sensitive_file(resolved):
        return False, f"Access to sensitive file '{path_str}' is denied."

    # Additional check on resolved filename for protected patterns
    if operation in ("write", "patch", "delete"):
        for protected in PROTECTED_WRITE_PATTERNS:
            if resolved.name.lower() == protected.lower():
                return False, f"{operation.capitalize()} operation on protected file '{protected}' is strictly forbidden."

    # Verify confinement within authorized directories
    # 1. Workspace
    from nivos.core.paths import get_workspace_dir

    try:
        ws_dir = get_workspace_dir().resolve()
        if resolved == ws_dir or resolved.is_relative_to(ws_dir):
            return True, ""
    except Exception:
        pass

    # 2. Canonical Vault Nivos-Inbox
    inbox_dir = _get_canonical_vault_inbox()
    if inbox_dir is not None:
        try:
            if resolved == inbox_dir or resolved.is_relative_to(inbox_dir):
                return True, ""
        except Exception:
            pass

    # 3. For read operations, also allow reading other non-sensitive notes in the vault
    if operation == "read":
        try:
            from nivos.tools.vault_tools import _get_vault_path

            vp = _get_vault_path()
            if vp is not None:
                vault_dir = vp.resolve()
                if resolved == vault_dir or resolved.is_relative_to(vault_dir):
                    if resolved.name.lower() == "me.md":
                        return False, "Reading me.md directly is restricted to profile loader."
                    return True, ""
        except Exception:
            pass

    # 4. Explicit or configured allowed_dirs
    if allowed_dirs is None:
        try:
            from nivos.core.config import load_config

            allowed_dirs = load_config().security.allowed_dirs
        except Exception:
            allowed_dirs = []

    if allowed_dirs:
        for d in allowed_dirs:
            try:
                ad_path = Path(d).expanduser().resolve()
                if resolved == ad_path or resolved.is_relative_to(ad_path):
                    return True, ""
            except Exception:
                continue

    return False, f"Target path '{path_str}' is outside workspace/, Nivos-Inbox/, and allowed directories."


def is_path_safe_for_write(path_str: str | Path, allowed_dirs: Optional[list[str]] = None) -> Tuple[bool, str]:
    """Validate that a target write path is inside workspace/ or Nivos-Inbox/ or allowed_dirs, and not protected."""
    return _validate_canonical_path(path_str, operation="write", allowed_dirs=allowed_dirs)


def is_path_safe_for_read(path_str: str | Path, allowed_dirs: Optional[list[str]] = None) -> Tuple[bool, str]:
    """Validate that a target read path is inside workspace/, vault, or allowed_dirs, and not sensitive."""
    return _validate_canonical_path(path_str, operation="read", allowed_dirs=allowed_dirs)


def is_path_safe_for_delete(path_str: str | Path, allowed_dirs: Optional[list[str]] = None) -> Tuple[bool, str]:
    """Validate that a target delete path is inside workspace/ or allowed_dirs, and not protected."""
    return _validate_canonical_path(path_str, operation="delete", allowed_dirs=allowed_dirs)


def is_path_safe_for_patch(path_str: str | Path, allowed_dirs: Optional[list[str]] = None) -> Tuple[bool, str]:
    """Validate that a target patch path is inside workspace/ or allowed_dirs, and not protected."""
    return _validate_canonical_path(path_str, operation="patch", allowed_dirs=allowed_dirs)


def is_tool_allowed(
    tool_name: str,
    *,
    origin: RequestOrigin | str = RequestOrigin.LOCAL_CHAT,
    is_untrusted_content: bool = False,
    params: Optional[Dict[str, Any]] = None,
    user_approved: bool = False,
) -> Tuple[bool, str]:
    """Central policy engine governing tool execution across origins and trust boundaries.

    Rules:
    1. Dangerous tools (shell, docker, open_app, self-improvement, writes outside workspace/ and Nivos-Inbox/)
       run ONLY on turns typed in local chat (origin == LOCAL_CHAT).
    2. Dangerous tools are NEVER available to scheduled tasks, Slack, or requests derived from untrusted content.
    3. Dangerous tools ALWAYS require approval.
    4. me.md is protected and cannot be written by any tool under any circumstances.
    """
    if not isinstance(origin, RequestOrigin):
        origin = RequestOrigin.from_str(origin)

    tool_name_clean = (tool_name or "").strip()

    # Rule 1 & 2: Untrusted content triggers fresh user approval for dangerous tools even if always allowed
    if is_untrusted_content and not user_approved:
        if tool_name_clean in DANGEROUS_TOOLS or tool_name_clean in ("file_write", "save_to_inbox"):
            return False, (
                f"Tool '{tool_name_clean}' triggered by untrusted content requires fresh user approval."
            )

    # Rule 1 & 2: Origins other than LOCAL_CHAT cannot use dangerous tools or file writes
    if origin != RequestOrigin.LOCAL_CHAT:
        if origin in (RequestOrigin.SLACK, RequestOrigin.CHANNEL) and tool_name_clean not in SAFE_READONLY_TOOLS:
            return False, (
                f"Tool '{tool_name_clean}' is not allowed for origin '{origin.value}'. "
                f"Channel requests may use only read-only tools ({', '.join(sorted(SAFE_READONLY_TOOLS))})."
            )
        if tool_name_clean in DANGEROUS_TOOLS:
            return False, (
                f"Dangerous tool '{tool_name_clean}' is not allowed for origin '{origin.value}'. "
                "Only local chat turns typed by the user may invoke dangerous tools."
            )
        if tool_name_clean in ("file_write", "save_to_inbox"):
            return False, (
                f"File write tool '{tool_name_clean}' is not allowed for origin '{origin.value}'. "
                "Only local chat turns typed by the user may perform file writes."
            )

    # Local chat validation for file writes
    if tool_name_clean == "file_write":
        path_arg = (params or {}).get("path", "")
        if path_arg:
            safe, reason = is_path_safe_for_write(path_arg)
            if not safe:
                return False, reason

    # Local chat validation for self-improvement target files
    if tool_name_clean == "self_improve" and params:
        files = params.get("files_to_modify", [])
        if files:
            from nivos.self_dev.policy import is_path_protected

            for f in files:
                prot, reason = is_path_protected(f)
                if prot:
                    return False, f"Self-improvement blocked from touching protected file '{f}': {reason}"

    # Local chat dangerous tools require approval
    if tool_name_clean in DANGEROUS_TOOLS:
        # Policy requires approval; the tool executor or interactive prompt must confirm
        return True, "Allowed for local chat with approval requirement."

    return True, ""


__all__ = [
    "DANGEROUS_TOOLS",
    "PROTECTED_WRITE_PATTERNS",
    "RequestOrigin",
    "SAFE_READONLY_TOOLS",
    "is_path_safe_for_delete",
    "is_path_safe_for_patch",
    "is_path_safe_for_read",
    "is_path_safe_for_write",
    "is_tool_allowed",
]
