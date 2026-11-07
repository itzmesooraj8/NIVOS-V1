"""Dedicated terminal consent for optional external usage analytics (disabled)."""

from __future__ import annotations

import tomlkit


def ask_analytics_consent(host: str | None = None) -> bool:
    """Analytics consent prompt is disabled; always returns False."""
    return False


def apply_analytics_choice(content: str, choice: bool | None = None, *, prompt: bool = False) -> str:
    """Ensure analytics remains disabled with no terminal prompt."""
    doc = tomlkit.parse(content)
    if "analytics" not in doc:
        doc.add("analytics", tomlkit.table())
    doc["analytics"]["enabled"] = False
    doc["analytics"]["choice_recorded"] = True
    return tomlkit.dumps(doc)

