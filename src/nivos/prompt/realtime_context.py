"""Real-time date, time, timezone, and location context injection for Nivos."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

logger = logging.getLogger(__name__)

# Trigger keywords for queries where user location is helpful
_LOCATION_KEYWORDS = re.compile(
    r"\b(weather|temperature|forecast|rain|raining|snow|humidity|wind|windy|storm|"
    r"sunny|cloudy|hot|cold|today|tomorrow|tonight|local|nearby|here|outside|"
    r"sunset|sunrise|dusk|dawn|traffic|news|events|restaurants|stores|food)\b",
    re.IGNORECASE,
)


def get_current_time_str(timezone_name: str = "Asia/Kolkata") -> str:
    """Format the current time in the specified timezone with date, AM/PM, and offset."""
    try:
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
    except Exception as exc:
        logger.debug("Failed to resolve timezone '%s' (%s); using local time", timezone_name, exc)
        now = datetime.now().astimezone()

    # Formatted as: Sunday, November 08, 2026, 10:48 PM IST (UTC+0530)
    formatted = now.strftime("%A, %B %d, %Y, %I:%M %p")
    tz_abbr = now.strftime("%Z")
    tz_offset = now.strftime("%z")
    offset_str = f"UTC{tz_offset[:3]}:{tz_offset[3:]}" if len(tz_offset) == 5 else f"UTC{tz_offset}"
    return f"{formatted} {tz_abbr} ({offset_str})".strip()


def is_location_relevant(query: str) -> bool:
    """Determine if user location is relevant to the query."""
    if not query:
        return False
    return bool(_LOCATION_KEYWORDS.search(query))


def build_realtime_prompt_context(
    query: str = "",
    *,
    location: str = "Palakkad, Kerala, India",
    timezone_name: str = "Asia/Kolkata",
    force_location: bool = False,
) -> str:
    """Assemble a real-time context block to inject into the system prompt.

    Date, time, and timezone are always injected. Location is only injected when
    relevant to the query or explicitly forced (e.g. for weather lookups).
    No IP lookups are performed.
    """
    time_str = get_current_time_str(timezone_name)
    lines = [
        "## Real-Time Context",
        f"- Current Date and Time: {time_str}",
        f"- Timezone: {timezone_name}",
    ]
    if force_location or is_location_relevant(query):
        loc = (location or "Palakkad, Kerala, India").strip()
        if loc:
            lines.append(f"- User Location: {loc}")

    return "\n".join(lines)


__all__ = [
    "build_realtime_prompt_context",
    "get_current_time_str",
    "is_location_relevant",
]

