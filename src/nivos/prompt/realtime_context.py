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

# Trigger keywords for queries where time context is relevant
_TIME_RELEVANCE_KEYWORDS = re.compile(
    r"\b(time|date|day|today|tomorrow|yesterday|tonight|clock|hour|hours|minute|minutes|"
    r"now|current|schedule|weather|calendar|morning|afternoon|evening|night)\b",
    re.IGNORECASE,
)

# Broadened patterns for pure time questions (including typos like wat, tiem, etc.)
_TIME_PATTERNS = [
    r"^(?:(?:what|wat|waht|wht)(?:'s|\s+is|\s+are)?\s+)?(?:the\s+|this\s+)?(?:current\s+)?(?:time|tiem)(?:\s+(?:now|right\s+now|today|please|currently))?$",
    r"^(?:what|wat|waht|wht)\s+(?:time|tiem)\s+is\s+it(?:\s+(?:now|right\s+now|today|currently))?$",
    r"^(?:tell\s+me|show\s+me|give\s+me)\s+(?:the\s+|this\s+)?(?:current\s+)?(?:time|tiem)(?:\s+now)?$",
    r"^(?:current\s+)?(?:time|tiem)(?:\s+(?:now|check|please))?$",
    r"^(?:what\s+is\s+)?time\s+now$",
]

# Broadened patterns for pure date questions
_DATE_PATTERNS = [
    r"^(?:(?:what|wat|waht|wht)(?:'s|\s+is)?\s+)?(?:the\s+|this\s+)?(?:current\s+)?(?:date|dat)(?:\s+(?:today|now))?$",
    r"^(?:(?:what|wat|waht|wht)(?:'s|\s+is)?\s+)?today(?:'s)?\s+(?:date|dat)$",
    r"^(?:what|wat|waht|wht)\s+(?:date|dat)\s+is\s+(?:it|today)(?:\s+now)?$",
    r"^(?:tell\s+me|show\s+me)\s+(?:today(?:'s)?\s+|the\s+)?(?:date|dat)$",
    r"^(?:current\s+)?(?:date|dat)(?:\s+today|\s+now)?$",
    r"^today(?:'s)?\s+(?:date|dat)$",
    r"^todays\s+date$",
    r"^date\s+today$",
]

# Broadened patterns for pure day-of-week questions
_DAY_PATTERNS = [
    r"^(?:what|wat|waht|wht)\s+day\s+is\s+(?:it|today)(?:\s+now)?$",
    r"^(?:(?:what|wat|waht|wht)(?:'s|\s+is)?\s+)?(?:the\s+)?day\s+today$",
    r"^(?:what|wat|waht|wht)\s+day\s+of\s+(?:the\s+)?week\s+is\s+(?:it|today)$",
    r"^(?:which|wich)\s+day\s+is\s+(?:it|today)$",
    r"^today(?:'s)?\s+day$",
    r"^what\s+day$",
]

# Combined date and time patterns
_DATETIME_PATTERNS = [
    r"^(?:(?:what|wat|waht|wht)(?:'s|\s+is)?\s+)?(?:the\s+|this\s+)?(?:current\s+)?(?:date\s+and\s+time|time\s+and\s+date)(?:\s+(?:now|today))?$",
    r"^(?:what|wat|waht|wht)\s+day\s+and\s+time\s+is\s+it(?:\s+now)?$",
    r"^(?:current\s+)?(?:date\s+and\s+time|time\s+and\s+date)(?:\s+now)?$",
]

_ALL_DATETIME_PATTERNS = _TIME_PATTERNS + _DATE_PATTERNS + _DAY_PATTERNS + _DATETIME_PATTERNS


def is_direct_datetime_query(query: str) -> bool:
    """Check if query is asking strictly for the current time, date, or day.

    Returns True only for standalone inquiries, avoiding false positives on
    queries such as 'time complexity' or 'flight departure time'.
    """
    if not query:
        return False
    # Strip terminal punctuation and whitespace
    cleaned = re.sub(r"[?!.,;:]+$", "", query.strip().lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return False
    return any(bool(re.match(pat, cleaned, re.IGNORECASE)) for pat in _ALL_DATETIME_PATTERNS)


def format_direct_datetime_response(
    query: str,
    *,
    timezone_name: str = "Asia/Kolkata",
    location: str = "Palakkad, Kerala, India",
) -> str:
    """Format an immediate, model-free response for date/time/day inquiries."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    time_str = now.strftime("%I:%M %p").lstrip("0")
    date_str = now.strftime("%B %d, %Y")
    day_str = now.strftime("%A")
    tz_abbr = now.strftime("%Z") or timezone_name
    loc = (location or "Palakkad, Kerala, India").strip()

    cleaned = re.sub(r"[?!.,;:]+$", "", query.strip().lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Pure day inquiry
    if any(bool(re.match(pat, cleaned, re.IGNORECASE)) for pat in _DAY_PATTERNS):
        return f"Today is **{day_str}**, {date_str}."

    # Pure date inquiry
    if any(bool(re.match(pat, cleaned, re.IGNORECASE)) for pat in _DATE_PATTERNS):
        return f"Today's date is **{day_str}, {date_str}**."

    # Time or combined date/time inquiry
    loc_part = f" in {loc}" if loc else ""
    return (
        f"It is currently **{time_str}** on **{day_str}, {date_str}** "
        f"({tz_abbr}, {timezone_name}){loc_part}."
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


def is_time_relevant(query: str) -> bool:
    """Determine if current time context is relevant to the query."""
    if not query:
        return False
    return bool(_TIME_RELEVANCE_KEYWORDS.search(query))


def build_realtime_prompt_context(
    query: str = "",
    *,
    location: str = "Palakkad, Kerala, India",
    timezone_name: str = "Asia/Kolkata",
    force_location: bool = False,
    force_time: bool = False,
) -> str:
    """Assemble a neutral, non-directive real-time clock context [now: ...].

    Injects timestamp only when query is time-relevant (or explicitly forced).
    Contains ZERO directive text to prevent leakage into assistant responses.
    """
    if not (force_time or force_location or is_time_relevant(query) or is_location_relevant(query)):
        return ""

    time_str = get_current_time_str(timezone_name)
    parts = [f"now: {time_str}"]

    if force_location or is_location_relevant(query):
        loc = (location or "Palakkad, Kerala, India").strip()
        if loc:
            parts.append(f"location: {loc}")

    return f"[{' | '.join(parts)}]"


def strip_realtime_context(text: str) -> str:
    """Strip injected real-time clock context from text if present."""
    if not text:
        return ""
    # Strip neutral format [now: ...]
    cleaned = re.sub(r"^\[now:[^\]]+\]\s*", "", text).strip()
    # Strip legacy format if present
    if "## Real-Time Context" in cleaned:
        marker = "Always use the authoritative timestamp above for the present moment."
        if marker in cleaned:
            cleaned = cleaned.split(marker)[-1].strip()
    return cleaned


__all__ = [
    "build_realtime_prompt_context",
    "format_direct_datetime_response",
    "get_current_time_str",
    "is_direct_datetime_query",
    "is_location_relevant",
    "is_time_relevant",
    "strip_realtime_context",
]
