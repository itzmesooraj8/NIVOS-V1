"""Claim Guard — verifies assistant claims against actual tool executions.

If an assistant response claims an action (created, saved, deleted, opened,
installed, scheduled) but no matching tool call succeeded in the current turn,
the claim is replaced with: "No tool ran, so nothing was changed."
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Set

logger = logging.getLogger(__name__)

# Action categories and their detection patterns
ACTION_CATEGORIES = [
    {
        "category": "create_or_save",
        "pattern": re.compile(
            r"\b(?:"
            r"i(?:'ve|\s+have)?\s+(?:successfully\s+)?(?:created|saved|written|wrote)|"
            r"(?:successfully\s+)?(?:created|saved|wrote)\s+(?:the\s+)?(?:file|folder|directory|note|document)|"
            r"(?:file|folder|note|directory|document)\s+(?:has\s+been|was)\s+(?:successfully\s+)?(?:created|saved|written)|"
            r"saved\s+(?:the\s+)?(?:file|note|data|content)?\s*(?:to|in)\s+"
            r")\b",
            re.IGNORECASE,
        ),
        "matching_tools": {"file_write", "save_to_inbox"},
    },
    {
        "category": "delete",
        "pattern": re.compile(
            r"\b(?:"
            r"i(?:'ve|\s+have)?\s+(?:successfully\s+)?(?:deleted|removed)|"
            r"(?:successfully\s+)?(?:deleted|removed)\s+(?:the\s+)?(?:file|folder|directory|note)|"
            r"(?:file|folder|note|directory)\s+(?:has\s+been|was)\s+(?:successfully\s+)?(?:deleted|removed)"
            r")\b",
            re.IGNORECASE,
        ),
        "matching_tools": {"file_delete", "delete_file"},
    },
    {
        "category": "open",
        "pattern": re.compile(
            r"\b(?:"
            r"i(?:'ve|\s+have)?\s+(?:successfully\s+)?(?:opened|launched)|"
            r"(?:successfully\s+)?(?:opened|launched)\s+(?:the\s+)?(?:app|application|program|window)|"
            r"(?:app|application|program)\s+(?:has\s+been|was)\s+(?:successfully\s+)?(?:opened|launched)"
            r")\b",
            re.IGNORECASE,
        ),
        "matching_tools": {"open_app", "file_read"},
    },
    {
        "category": "install",
        "pattern": re.compile(
            r"\b(?:"
            r"i(?:'ve|\s+have)?\s+(?:successfully\s+)?installed|"
            r"(?:successfully\s+)?installed\s+(?:the\s+)?(?:package|dependency|dependencies|library|module|tool)|"
            r"(?:package|dependency|dependencies|library|module)\s+(?:has\s+been|was)\s+(?:successfully\s+)?installed"
            r")\b",
            re.IGNORECASE,
        ),
        "matching_tools": {"sandboxed_shell", "docker_shell", "shell_exec"},
    },
    {
        "category": "schedule",
        "pattern": re.compile(
            r"\b(?:"
            r"i(?:'ve|\s+have)?\s+(?:successfully\s+)?scheduled|"
            r"(?:successfully\s+)?scheduled\s+(?:the\s+)?(?:task|reminder|job|alarm|cron)|"
            r"(?:task|reminder|job|alarm)\s+(?:has\s+been|was)\s+(?:successfully\s+)?scheduled"
            r")\b",
            re.IGNORECASE,
        ),
        "matching_tools": {"schedule_task"},
    },
]

GUARD_REPLACEMENT = "No tool ran, so nothing was changed."


def verify_and_guard_claims(
    content: str,
    tool_results: Optional[List[Any]] = None,
) -> str:
    """Inspect assistant response content for action claims.

    If an action is claimed but no matching tool succeeded during this turn,
    replace the claim with: "No tool ran, so nothing was changed."
    """
    if not content or not isinstance(content, str):
        return content

    # Find successful tool executions in this turn
    succeeded_tools: Set[str] = set()
    for tr in (tool_results or []):
        t_name = getattr(tr, "tool_name", "") or getattr(tr, "name", "")
        t_success = getattr(tr, "success", True)
        if t_name and t_success:
            succeeded_tools.add(t_name)

    # Check each action category
    for cat in ACTION_CATEGORIES:
        pat = cat["pattern"]
        if pat.search(content):
            matching = cat["matching_tools"]
            if not (matching & succeeded_tools):
                logger.warning(
                    "Claim Guard intercepted unverified claim in category '%s'. "
                    "Claimed in content: %r. Succeeded tools: %r. Replacing claim.",
                    cat["category"],
                    content[:120],
                    succeeded_tools,
                )
                return GUARD_REPLACEMENT

    return content


__all__ = ["GUARD_REPLACEMENT", "verify_and_guard_claims"]

