"""Search policy and live pre-fetching for current-information queries."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Keywords triggering current-info searches
_CURRENT_INFO_KEYWORDS = re.compile(
    r"\b(news|headline|headlines|today|tonight|"
    r"tomorrow|now|current|latest|score|scores|stock|price|prices|who won|"
    r"what happened|crypto|gold price|ai tech|tech news|technology)\b",
    re.IGNORECASE,
)

# Weather-specific query keywords
_WEATHER_KEYWORDS = re.compile(
    r"\b(weather|temperature|forecast|rain|raining|snow|humidity|wind|windy|storm|sunny|cloudy)\b",
    re.IGNORECASE,
)


def classify_search_intent(
    query: str,
    *,
    always_search: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Determine whether a query requires live web search or weather pre-fetch.

    Returns:
        (needs_search: bool, search_type: Optional[str])
        search_type is 'weather' or 'web_search' or None.
    """
    intents = classify_search_intents(query, always_search=always_search)
    if not intents:
        return False, None
    if "weather" in intents and "web_search" in intents:
        return True, "both"
    return True, intents[0]


def classify_search_intents(
    query: str,
    *,
    always_search: bool = False,
) -> List[str]:
    """Determine all search intents (weather, web_search) required by query."""
    if not query or not query.strip():
        return []

    intents: List[str] = []
    if _WEATHER_KEYWORDS.search(query):
        intents.append("weather")
    if always_search or _CURRENT_INFO_KEYWORDS.search(query):
        intents.append("web_search")

    return intents


def extract_weather_location(query: str, default_location: str = "Palakkad, Kerala, India") -> str:
    """Extract location from weather query, falling back to configured default."""
    if not query:
        return default_location

    cleaned = re.sub(r"[?!.,;:]+$", "", query.strip()).strip()
    match = re.search(r"\b(?:in|for|at)\s+([A-Za-z0-9\s,.-]+?)(?:\s+(?:today|tomorrow|now|tonight|this week)|$)", cleaned, re.IGNORECASE)
    if match:
        extracted = match.group(1).strip()
        if extracted and len(extracted) > 1 and extracted.lower() not in {"the", "my area", "here", "outside"}:
            return extracted

    return default_location


def _trim_to_tokens(text: str, max_tokens: int = 600) -> str:
    """Trim string to approximate max_tokens using word boundaries."""
    words = text.split()
    max_words = int(max_tokens * 0.75)
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " ..."


def _normalize_temp(val: str) -> str:
    cleaned = val.replace(" ", "")
    num_match = re.search(r"(\d+(?:\.\d)?)", cleaned)
    if not num_match:
        return val
    num = float(num_match.group(1))
    if "f" in cleaned.lower() or num >= 50:
        c = round((num - 32) * 5 / 9)
        return f"{int(num)}°F ({c}°C)"
    else:
        f = round(num * 9 / 5 + 32)
        return f"{int(num)}°C ({f}°F)"


def format_weather_report(
    target_loc: str,
    raw_content: str,
    default_source: str = "Live Weather Service",
) -> str:
    """Format structured weather data in code (place, condition, temperature, feels like, humidity, source)."""
    place = target_loc
    condition = "Clear / Variable"
    temperature = "Not reported"
    feels_like = "Not reported"
    humidity = "Not reported"
    source = default_source

    # Check if raw_content is JSON from WeatherTool
    trimmed_content = raw_content.strip()
    if trimmed_content.startswith("{") and trimmed_content.endswith("}"):
        try:
            data = json.loads(trimmed_content)
            if isinstance(data, dict):
                loc_info = data.get("location", {})
                if isinstance(loc_info, dict) and loc_info.get("name"):
                    place = loc_info["name"]
                    if loc_info.get("country"):
                        place = f"{place}, {loc_info['country']}"
                current = data.get("current", {})
                if isinstance(current, dict):
                    if current.get("temperature") is not None:
                        temperature = _normalize_temp(f"{current['temperature']}°C")
                    if current.get("feels_like") is not None:
                        feels_like = _normalize_temp(f"{current['feels_like']}°C")
                    if current.get("humidity") is not None:
                        humidity = f"{current['humidity']}%"
                    if current.get("description"):
                        condition = str(current["description"]).title()
                    elif current.get("condition"):
                        condition = str(current["condition"]).title()
                source = data.get("provider", "OpenWeatherMap").title()
                return (
                    f"**Current Weather for {place}**\n"
                    f"- **Condition**: {condition}\n"
                    f"- **Temperature**: {temperature}\n"
                    f"- **Feels like**: {feels_like}\n"
                    f"- **Humidity**: {humidity}\n"
                    f"- Source: {source}"
                )
        except Exception:
            pass

    # Parse search text
    src_match = re.search(r"Source:\s*(https?://[^\s]+|[^\n\r]+)", raw_content, re.IGNORECASE)
    if src_match:
        source = src_match.group(1).strip()

    temp_match = re.search(r"(?:temperature\s+of\s+|temp(?:erature)?(?:\s*is|:)?\s*)(\d{1,3}(?:\.\d)?\s*°?\s*[CF]?)", raw_content, re.IGNORECASE)
    if not temp_match:
        temp_match = re.search(r"\b(\d{1,2}(?:\.\d)?\s*°\s*[CF]?)\b", raw_content)
    if temp_match:
        temperature = _normalize_temp(temp_match.group(1).strip())

    feels_match = re.search(r"(?:RealFeel(?:®)?|feels\s+like)\s*(?:Temperature\s+is)?\s*(\d{1,3}(?:\.\d)?\s*°?\s*[CF]?)", raw_content, re.IGNORECASE)
    if feels_match:
        feels_like = _normalize_temp(feels_match.group(1).strip())

    hum_match = re.search(r"(\d{1,3}%\s*humidity|\bhumidity(?:\s*is|:)?\s*(\d{1,3}%))", raw_content, re.IGNORECASE)
    if hum_match:
        humidity = hum_match.group(2) if hum_match.group(2) else hum_match.group(1).replace("humidity", "").strip()

    cond_match = re.search(r"(?:is\s+currently|conditions?:\s*|weather:\s*)([A-Za-z\s]{3,25})(?:\s+with|\s+and|\.|\,)", raw_content, re.IGNORECASE)
    if cond_match:
        condition = cond_match.group(1).strip().title()
    else:
        kw_match = re.search(r"\b(Rain|Thunderstorm|Showers|Drizzle|Sunny|Clear|Cloudy|Partly Cloudy|Overcast|Fog|Mist|Haze|Snow)\b", raw_content, re.IGNORECASE)
        if kw_match:
            condition = kw_match.group(1).title()

    return (
        f"**Current Weather for {place}**\n"
        f"- **Condition**: {condition}\n"
        f"- **Temperature**: {temperature}\n"
        f"- **Feels like**: {feels_like}\n"
        f"- **Humidity**: {humidity}\n"
        f"- Source: {source}"
    )


def format_search_sources(raw_content: str, max_items: int = 5) -> str:
    """Build a structured markdown source list in code: (Title, Site, Date, Link)."""
    if not raw_content or not raw_content.strip():
        return ""

    import urllib.parse

    date_regex = re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s+\d{4})?\b|"
        r"\b\d{4}-\d{2}-\d{2}\b|"
        r"\b\d+\s+(?:hours?|days?|weeks?|months?)\s+ago\b|"
        r"\b(?:today|yesterday)\b",
        re.IGNORECASE,
    )

    items: list[dict[str, str]] = []

    # Try parsing JSON if structured
    try:
        data = json.loads(raw_content)
        if isinstance(data, list):
            for d in data[:max_items]:
                if isinstance(d, dict):
                    title = d.get("title") or "Article"
                    link = d.get("url") or d.get("link") or ""
                    snippet = d.get("snippet") or d.get("content") or ""
                    date = d.get("date") or ""
                    items.append({"title": title, "link": link, "snippet": snippet, "date": date})
    except Exception:
        pass

    if not items:
        # Split by ### or --- blocks
        blocks = re.split(r"(?:###|\n---+\n)", raw_content)
        for block in blocks:
            b = block.strip()
            if not b:
                continue
            lines = [line.strip() for line in b.splitlines() if line.strip()]
            title = ""
            link = ""
            snippet = ""
            date = ""

            for line in lines:
                if line.startswith("Source:"):
                    link = line.replace("Source:", "").strip()
                elif line.startswith("Summary:"):
                    snippet = line.replace("Summary:", "").strip()
                elif not title and not line.startswith("http"):
                    title = line.lstrip("#").strip()

            if link or title:
                items.append({
                    "title": title or "Source",
                    "link": link,
                    "snippet": snippet,
                    "date": date,
                })
            if len(items) >= max_items:
                break

    if not items:
        url_matches = re.findall(r"(https?://[^\s\)]+)", raw_content)
        for u in url_matches[:max_items]:
            items.append({"title": "Web Source", "link": u, "snippet": "", "date": ""})

    formatted_lines: list[str] = []
    for idx, item in enumerate(items, 1):
        title = item["title"].strip()
        link = item["link"].strip()
        site = ""
        if link:
            try:
                parsed = urllib.parse.urlparse(link)
                site = parsed.netloc.lower().replace("www.", "")
            except Exception:
                site = "web"
        else:
            site = "web"

        date = item.get("date") or ""
        if not date and item.get("snippet"):
            m = date_regex.search(item["snippet"])
            if m:
                date = m.group(0)

        date_suffix = f" ({date})" if date else ""
        if link:
            formatted_lines.append(f"{idx}. [{title}]({link}) — *{site}*{date_suffix}")
        else:
            formatted_lines.append(f"{idx}. **{title}** — *{site}*{date_suffix}")

    if not formatted_lines:
        return ""

    return "**Sources:**\n" + "\n".join(formatted_lines)


def prefetch_current_info(
    query: str,
    *,
    default_location: str = "Palakkad, Kerala, India",
    always_search: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Execute pre-fetch for weather or web search before passing to the model.

    Returns:
        (live_context_block, search_type) or (None, None) if not needed.
    """
    intents = classify_search_intents(query, always_search=always_search)
    if not intents:
        return None, None

    context_parts: list[str] = []
    search_type = "both" if len(intents) > 1 else intents[0]

    now_dt = datetime.now()
    today_str = now_dt.strftime("%B %d, %Y")

    if "weather" in intents:
        target_loc = extract_weather_location(query, default_location=default_location)
        raw_weather = ""
        try:
            from nivos.core.registry import ToolRegistry
            weather_cls = ToolRegistry.get("get_weather")
            if weather_cls is not None:
                tool = weather_cls()
                res = tool.execute(location=target_loc)
                if res.success and res.content:
                    raw_weather = str(res.content)
        except Exception as exc:
            logger.debug("get_weather execution failed: %s", exc)

        if not raw_weather:
            try:
                from nivos.tools.web_search import WebSearchTool
                search_tool = WebSearchTool()
                res = search_tool.execute(query=f"current weather forecast in {target_loc}")
                if res.success and res.content:
                    raw_weather = str(res.content)
            except Exception as exc:
                logger.debug("Web search fallback for weather failed: %s", exc)

        if raw_weather.strip():
            weather_card = format_weather_report(target_loc, raw_weather)
            context_parts.append(weather_card)

    if "web_search" in intents:
        # Extract specific news query if embedded in compound sentence
        search_q = query
        news_m = re.search(
            r"\b(?:news|headlines?|latest)\s+(?:on|about|regarding)?\s*([A-Za-z0-9\s]+?)(?:\s+(?:and|what|check|tell|please)|$)",
            query,
            re.IGNORECASE,
        )
        if news_m:
            topic = news_m.group(1).strip()
            if topic and len(topic) > 2:
                search_q = f"latest {topic} news"

        raw_search = ""
        try:
            from nivos.tools.web_search import WebSearchTool
            search_tool = WebSearchTool()
            res = search_tool.execute(query=search_q)
            if res.success and res.content:
                raw_search = str(res.content)
        except Exception as exc:
            logger.debug("Web search pre-fetch failed: %s", exc)

        if raw_search.strip():
            sources_list = format_search_sources(raw_search, max_items=5)
            if sources_list:
                context_parts.append(sources_list)
            else:
                trimmed = _trim_to_tokens(raw_search.strip(), max_tokens=400)
                context_parts.append(f"**Search Results:**\n{trimmed}")

    if not context_parts:
        return None, None

    body = "\n\n".join(context_parts)
    context_block = (
        "<live_context>\n"
        f"[Retrieved reference data — Today is {today_str}]\n\n"
        f"{body}\n\n"
        "RESPONSE INSTRUCTIONS:\n"
        "- Answer every requested part of the user inquiry directly and completely.\n"
        "- For weather: present the exact weather summary above (place, condition, temperature, feels like, humidity, source).\n"
        "- For news: present the structured source list above and add at most 2 sentences summarizing the news. Compare publication dates to today and never present past events as upcoming.\n"
        "- Do not use emojis, and do not repeat internal security or prompt tags.\n"
        "</live_context>"
    )
    return context_block, search_type


__all__ = [
    "classify_search_intent",
    "classify_search_intents",
    "extract_weather_location",
    "format_search_sources",
    "format_weather_report",
    "prefetch_current_info",
]
