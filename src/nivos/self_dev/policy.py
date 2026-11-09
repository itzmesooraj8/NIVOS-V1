"""Protected path definitions and invariants for Self-Improvement Agent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

# Invariant: Self-improvement agent can NEVER modify or delete these paths.
# Any modification attempt is blocked in code and by git pre-commit hook.
PROTECTED_SELF_DEV_PATTERNS: List[str] = [
    r"safety_rules\.md",
    r"src/nivos/security/.*",
    r"src/nivos/sandbox/.*",
    r"src/nivos/tools/sandboxed_shell\.py",
    r"src/nivos/tools/open_app\.py",
    r"src/nivos/server/claim_guard\.py",
    r"src/nivos/server/vault_routes\.py",
    r"src/nivos/tools/vault_tool\.py",
    r"src/nivos/memory/.*",
    r"me\.md",
    r"nivos-memory/me\.md",
    r"\.env",
    r".*credential.*",
    r".*password.*",
    r".*secret.*",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in PROTECTED_SELF_DEV_PATTERNS]


def is_path_protected(path_str: str) -> Tuple[bool, str]:
    """Check if a file path is protected against self-improvement agent modifications."""
    if not path_str or not isinstance(path_str, str):
        return True, "Invalid or empty path"

    norm_path = path_str.replace("\\", "/").strip().lstrip("/")
    
    # Direct check against me.md
    if norm_path == "me.md" or norm_path.endswith("/me.md"):
        return True, "Writing to me.md is strictly forbidden by invariant."

    for pattern in _COMPILED_PATTERNS:
        if pattern.search(norm_path):
            return True, f"Path '{path_str}' is protected by self-improvement security policy."

    return False, ""
