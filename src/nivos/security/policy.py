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
    "self_improve",
}

# Tools explicitly permitted for read-only / channel / scheduled origins
SAFE_READONLY_TOOLS: Set[str] = {
    "web_search",
    "get_weather",
    "search_vault",
    "calculator",
    "time",
}

# Paths and file patterns that can NEVER be written to by any agent or tool
PROTECTED_WRITE_PATTERNS = [
    "me.md",
    "nivos-memory/me.md",
    "safety_rules.md",
    ".env",
]


def is_path_safe_for_write(path_str: str, allowed_dirs: Optional[list[str]] = None) -> Tuple[bool, str]:
    """Validate that a target write path is inside workspace/ or Nivos-Inbox/ or allowed_dirs, and not protected."""
    if not path_str or not isinstance(path_str, str):
        return False, "Target path is missing or invalid."

    norm_path = path_str.replace("\\", "/").lower()

    # Never allow writing, editing, or deleting me.md
    for protected in PROTECTED_WRITE_PATTERNS:
        if norm_path.endswith(protected) or f"/{protected}" in norm_path:
            return False, f"Writing to protected file '{protected}' is strictly forbidden."

    # Check sensitive file names
    from nivos.security.file_policy import is_sensitive_file

    target_p = Path(path_str).expanduser()
    if is_sensitive_file(target_p):
        return False, f"Writing to sensitive file '{path_str}' is denied."

    # Check allowed roots: workspace/, Nivos-Inbox/, or configured security.allowed_dirs
    from nivos.core.paths import get_workspace_dir

    try:
        resolved = target_p.resolve()
        ws_dir = get_workspace_dir().resolve()
        if resolved == ws_dir or resolved.is_relative_to(ws_dir):
            return True, ""
    except Exception:
        pass

    # Check Nivos-Inbox
    if "nivos-inbox" in norm_path:
        return True, ""

    # Check allowed_dirs if configured
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

    # Rule 1 & 2: Untrusted content can never invoke dangerous tools or perform file writes
    if is_untrusted_content:
        if tool_name_clean in DANGEROUS_TOOLS or tool_name_clean in ("file_write", "save_to_inbox"):
            return False, (
                f"Tool '{tool_name_clean}' cannot be executed from untrusted content "
                "(web results, notes, files, or external messages)."
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
    "is_path_safe_for_write",
    "is_tool_allowed",
]
