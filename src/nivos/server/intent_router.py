"""Deterministic intent router executing tools in code before model generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from nivos.core.config import NivosConfig, load_config
from nivos.core.paths import get_workspace_dir
from nivos.core.types import StepType, Trace, TraceStep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for the 10 supported intents
# ---------------------------------------------------------------------------

_PLAIN_CHAT_EXACT = re.compile(
    r"^(?:hi|hello|hey|howdy|sup|good\s+morning|good\s+afternoon|good\s+evening|good\s+night|"
    r"good!|good|great|nice|awesome|cool|ok|okay|perfect|done|thanks|thank\s+you|thx|"
    r"thank\s+you\s+so\s+much|who\s+are\s+you|what\s+can\s+you\s+do|how\s+are\s+you|"
    r"bye|goodbye|see\s+ya|ping|test)[!.,?]*$",
    re.IGNORECASE,
)

_TIME_DATE_PATTERN = re.compile(
    r"^(?:what(?:'s|\s+is)?\s+)?(?:the\s+)?(?:current\s+)?(?:time|date|day|clock)(?:\s+(?:is\s+it|now|today|right\s+now))?\??$|"
    r"\b(?:what\s+time\s+is\s+it|what\s+is\s+the\s+time|current\s+time|what\s+is\s+today's\s+date|what\s+date\s+is\s+today|what\s+day\s+is\s+today|day\s+of\s+the\s+week)\b",
    re.IGNORECASE,
)

_SYSTEM_STATS_PATTERN = re.compile(
    r"\b(?:how\s+much\s+(?:free\s+)?ram|ram\s+usage|free\s+ram|ram\s+left|system\s+ram|"
    r"cpu\s+usage|how\s+much\s+cpu|disk\s+space|free\s+disk|disk\s+usage|"
    r"system\s+stats|hardware\s+stats|resource\s+usage|system\s+memory)\b",
    re.IGNORECASE,
)

_WEATHER_PATTERN = re.compile(
    r"\b(weather|temperature|forecast|is\s+it\s+raining|how\s+hot|how\s+cold)\b",
    re.IGNORECASE,
)

_OPEN_APP_PATTERN = re.compile(
    r"^(?:open|launch|start)\s+(?:the\s+)?(?:app\s+)?([A-Za-z0-9_\-\.\:\s]+?)(?:\s+(?:app|application))?$",
    re.IGNORECASE,
)

_INSTALL_APP_PATTERN = re.compile(
    r"^(?:install|get|download\s+and\s+install)\s+(?:the\s+)?(?:app\s+)?([A-Za-z0-9_\-\.\s]+?)(?:\s+via\s+winget)?$",
    re.IGNORECASE,
)

_SAVE_NOTE_PATTERN = re.compile(
    r"^(?:save\s+(?:a\s+)?note|create\s+(?:a\s+)?note|note\s+that|add\s+to\s+inbox|save\s+to\s+obsidian)\s*[:\-]?\s*(.+)$",
    re.IGNORECASE,
)

_SEARCH_VAULT_PATTERN = re.compile(
    r"^(?:search\s+(?:the\s+|my\s+)?vault|search\s+(?:the\s+|my\s+)?notes|find\s+in\s+(?:the\s+|my\s+)?vault|find\s+in\s+(?:the\s+|my\s+)?notes)\s*(?:for\s+)?(.+)$",
    re.IGNORECASE,
)

_WEB_SEARCH_PATTERN = re.compile(
    r"^(?:web\s+search|search\s+(?:the\s+)?web|google|search\s+online)\s*(?:for\s+)?(.+)$",
    re.IGNORECASE,
)

_FILE_CREATE_PATTERN = re.compile(
    r"\b(?:create|write|make)\s+(?:a\s+)?(?:text\s+)?file\s+(?:in\s+)?([A-Za-z0-9_\-\.\/\\\:]+)(?:\s+with\s+(?:content\s+)?(.+))?",
    re.IGNORECASE,
)

_CALCULATOR_PATTERN = re.compile(
    r"^(?:calculate|calc|compute)\s+(.+)$",
    re.IGNORECASE,
)

_FILE_READ_PATTERN = re.compile(
    r"^(?:read|view|show|cat)\s+(?:the\s+)?file\s+([A-Za-z0-9_\-\.\/\\\:]+)$",
    re.IGNORECASE,
)


@dataclass
class IntentMatch:
    intent: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentExecutionResult:
    intent: str
    reply_text: str
    tool_name: Optional[str] = None
    tool_success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


def classify_intent(query: str) -> Optional[IntentMatch]:
    """Classify user query into one of the 10 deterministic intents, or return None."""
    if not query or not query.strip():
        return None
    cleaned = query.strip()

    # 1. Plain chat (greetings / pleasantries — NO search, NO tools)
    if _PLAIN_CHAT_EXACT.match(cleaned):
        return IntentMatch(intent="plain_chat", params={"query": cleaned})

    # 2. Time / Date
    if _TIME_DATE_PATTERN.search(cleaned):
        return IntentMatch(intent="time/date", params={"query": cleaned})

    # 3. System stats (RAM, CPU, disk)
    if _SYSTEM_STATS_PATTERN.search(cleaned):
        return IntentMatch(intent="system_stats", params={"query": cleaned})

    # 4. Open app (Fix: "open spotify" must use open_app, never shell)
    # Exclude open file requests or file extensions
    app_match = _OPEN_APP_PATTERN.match(cleaned)
    if app_match:
        cand = app_match.group(1).strip()
        cand_lower = cand.lower()
        if not cand_lower.startswith(("file", "doc", "http://", "https://")) and not any(
            cand_lower.endswith(ext) for ext in (".txt", ".py", ".md", ".json", ".csv", ".log", ".html")
        ):
            return IntentMatch(intent="open_app", params={"app": cand})

    # 5. File create (Fix: must never claim created unless receipt succeeds)
    file_match = _FILE_CREATE_PATTERN.search(cleaned)
    if file_match:
        target_path = file_match.group(1).strip().strip("'\"")
        content = file_match.group(2) or ""
        return IntentMatch(intent="file_create", params={"path": target_path, "content": content.strip()})

    # 6. Save note (Obsidian inbox)
    note_match = _SAVE_NOTE_PATTERN.match(cleaned)
    if note_match:
        body = note_match.group(1).strip()
        return IntentMatch(intent="save_note", params={"content": body})

    # 7. Search vault
    vault_match = _SEARCH_VAULT_PATTERN.match(cleaned)
    if vault_match:
        sq = vault_match.group(1).strip()
        return IntentMatch(intent="search_vault", params={"query": sq})

    # 8. Weather
    if _WEATHER_PATTERN.search(cleaned):
        # Extract location if specified e.g. "weather in Tokyo"
        q_clean = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
        loc_match = re.search(r"\b(?:in|for|at)\s+([A-Za-z0-9\s,.-]+?)(?:\s+(?:today|tomorrow|now)|$)", q_clean, re.IGNORECASE)
        loc = loc_match.group(1).strip() if loc_match else ""
        return IntentMatch(intent="weather", params={"location": loc, "query": cleaned})

    # 9. Install app
    install_match = _INSTALL_APP_PATTERN.match(cleaned)
    if install_match:
        app_cand = install_match.group(1).strip()
        return IntentMatch(intent="install_app", params={"app": app_cand})

    # 10. Web search
    web_match = _WEB_SEARCH_PATTERN.match(cleaned)
    if web_match:
        sq = web_match.group(1).strip()
        return IntentMatch(intent="web_search", params={"query": sq})

    # 11. Calculator
    calc_match = _CALCULATOR_PATTERN.match(cleaned)
    if calc_match:
        expr = calc_match.group(1).strip()
        return IntentMatch(intent="calculator", params={"expression": expr})

    # 12. File read
    file_read_match = _FILE_READ_PATTERN.match(cleaned)
    if file_read_match:
        fpath = file_read_match.group(1).strip().strip("'\"")
        return IntentMatch(intent="file_read", params={"path": fpath})

    return None


def execute_intent(
    match: IntentMatch,
    config: Optional[NivosConfig] = None,
    trace_store: Any = None,
) -> IntentExecutionResult:
    """Execute the tool directly in code and format the single authoritative reply."""
    cfg = config or load_config()
    intent = match.intent
    params = match.params
    user_query = str(params.get("query", ""))

    reply_text = ""
    tool_name: Optional[str] = None
    tool_success = True
    metadata: Dict[str, Any] = {"intent": intent}

    # 1. Plain chat
    if intent == "plain_chat":
        tool_name = "plain_chat"
        q_low = user_query.lower()
        if any(w in q_low for w in ("hi", "hello", "hey", "howdy", "sup")):
            reply_text = "Hello! How can I help you today?"
        elif any(w in q_low for w in ("good!", "great", "nice", "awesome", "cool", "perfect", "done")):
            reply_text = "Great to hear! Let me know if you need anything else."
        elif "thank" in q_low or "thx" in q_low:
            reply_text = "You're welcome! Glad I could help."
        elif "who are you" in q_low:
            reply_text = "I am Nivos, your private on-device AI assistant."
        elif "how are you" in q_low:
            reply_text = "I'm running well and ready to assist you!"
        elif any(w in q_low for w in ("bye", "goodbye", "see ya")):
            reply_text = "Goodbye! Have a great day."
        else:
            reply_text = "Hello! Let me know what you would like to work on."

    # 2. Time / Date
    elif intent == "time/date":
        tool_name = "time_tool"
        tz_name = getattr(getattr(cfg, "user", None), "timezone", "Asia/Kolkata") or "Asia/Kolkata"
        user_loc = getattr(getattr(cfg, "user", None), "location", "Palakkad, Kerala, India") or "Palakkad, Kerala, India"
        try:
            now_dt = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now_dt = datetime.now()
        time_str = now_dt.strftime("%I:%M %p").lstrip("0")
        date_str = now_dt.strftime("%A, %B %d, %Y")
        reply_text = f"The current time in {user_loc} ({tz_name}) is {time_str} on {date_str}."

    # 3. System stats (RAM, CPU, disk)
    elif intent == "system_stats":
        tool_name = "system_stats"
        try:
            import psutil

            vm = psutil.virtual_memory()
            free_ram_gb = vm.available / (1024**3)
            total_ram_gb = vm.total / (1024**3)
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except Exception:
            free_ram_gb, total_ram_gb, cpu_pct = 0.0, 0.0, 0.0

        try:
            target_p = Path.home()
            du = shutil.disk_usage(target_p)
            free_disk_gb = du.free / (1024**3)
            disk_anchor = target_p.anchor or "C:"
        except Exception:
            free_disk_gb = 0.0
            disk_anchor = "Disk"

        reply_text = (
            f"System Stats: RAM {free_ram_gb:.1f} GB free / {total_ram_gb:.1f} GB total "
            f"({vm.percent:.0f}% used) | CPU {cpu_pct:.0f}% | {disk_anchor} {free_disk_gb:.1f} GB free."
        )

    # 4. Open app (open spotify -> open_app tool, NEVER shell)
    elif intent == "open_app":
        tool_name = "open_app"
        app_name = params.get("app", "")
        try:
            from nivos.tools.open_app import OpenAppTool

            tool = OpenAppTool()
            res = tool.execute(app=app_name, origin="local_chat")
            tool_success = bool(res.success)
            if res.success:
                reply_text = f"Opened {app_name}."
            else:
                reply_text = f"Could not open {app_name}: {res.content}"
        except Exception as exc:
            tool_success = False
            reply_text = f"Could not open {app_name}: {exc}"

    # 5. File create (must never claim created unless tool receipt says success)
    elif intent == "file_create":
        tool_name = "file_write"
        rel_path = params.get("path", "")
        content = params.get("content", "")

        # Target workspace/ directory by default
        ws_dir = get_workspace_dir()
        if not (rel_path.startswith("workspace/") or rel_path.startswith("workspace\\")):
            full_path = (ws_dir / rel_path).resolve()
        else:
            full_path = Path(rel_path).resolve()

        try:
            from nivos.tools.file_write import FileWriteTool

            tool = FileWriteTool(allowed_dirs=[str(ws_dir)])
            res = tool.execute(
                path=str(full_path),
                content=content,
                mode="write",
                create_dirs=True,
                origin="local_chat",
            )
            tool_success = bool(res.success)
            # CRITICAL RULE: A file request must never end with a "created" claim
            # unless the tool receipt says success.
            if res.success:
                short_name = full_path.name
                reply_text = f"Created file {short_name} in workspace/."
            else:
                reply_text = f"Failed to create file: {res.content}"
        except Exception as exc:
            tool_success = False
            reply_text = f"Failed to create file: {exc}"

    # 6. Save note to Obsidian inbox
    elif intent == "save_note":
        tool_name = "save_to_inbox"
        note_body = params.get("content", "")
        ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"note_{ts_slug}.md"
        try:
            from nivos.tools.vault_tools import SaveToInboxTool

            tool = SaveToInboxTool()
            res = tool.execute(filename=filename, content=note_body)
            tool_success = bool(res.success)
            if res.success:
                reply_text = f"Saved note to Obsidian inbox: Nivos-Inbox/{filename}."
            else:
                reply_text = f"Could not save note: {res.content}"
        except Exception as exc:
            tool_success = False
            reply_text = f"Could not save note: {exc}"

    # 7. Search vault
    elif intent == "search_vault":
        tool_name = "search_vault"
        sq = params.get("query", "")
        try:
            from nivos.tools.vault_tools import SearchVaultTool

            tool = SearchVaultTool()
            res = tool.execute(query=sq)
            tool_success = bool(res.success)
            reply_text = res.content
        except Exception as exc:
            tool_success = False
            reply_text = f"Vault search failed: {exc}"

    # 8. Weather
    elif intent == "weather":
        tool_name = "get_weather"
        loc = params.get("location") or getattr(getattr(cfg, "user", None), "location", "Palakkad, Kerala, India") or "Palakkad, Kerala, India"
        try:
            from nivos.server.search_policy import fetch_open_meteo_weather, format_weather_report

            raw_report = fetch_open_meteo_weather(loc)
            if raw_report:
                reply_text = format_weather_report(loc, raw_report)
            else:
                reply_text = f"Could not retrieve current weather for {loc}."
        except Exception as exc:
            tool_success = False
            reply_text = f"Could not retrieve weather: {exc}"

    # 9. Install app
    elif intent == "install_app":
        tool_name = "install_app"
        app_name = params.get("app", "").strip()
        common_pkgs = {
            "vlc": "VideoLAN.VLC",
            "spotify": "Spotify.Spotify",
            "git": "Git.Git",
            "chrome": "Google.Chrome",
            "firefox": "Mozilla.Firefox",
            "vscode": "Microsoft.VisualStudioCode",
            "7zip": "7zip.7zip",
        }
        pkg_id = common_pkgs.get(app_name.lower(), app_name)
        try:
            from nivos.tools.install_app import InstallAppTool

            tool = InstallAppTool()
            res = tool.execute(package_id=pkg_id, app=pkg_id, origin="local_chat")
            tool_success = bool(res.success)
            reply_text = res.content
        except Exception as exc:
            tool_success = False
            reply_text = f"Install app failed: {exc}"

    # 10. Web search
    elif intent == "web_search":
        tool_name = "web_search"
        sq = params.get("query", "")
        try:
            from nivos.tools.web_search import WebSearchTool

            tool = WebSearchTool()
            res = tool.execute(query=sq)
            tool_success = bool(res.success)
            reply_text = res.content
        except Exception as exc:
            tool_success = False
            reply_text = f"Web search failed: {exc}"

    # 11. Calculator
    elif intent == "calculator":
        tool_name = "calculator"
        expr = params.get("expression", "")
        try:
            from nivos.tools.calculator import CalculatorTool

            tool = CalculatorTool()
            res = tool.execute(expression=expr)
            tool_success = bool(res.success)
            reply_text = f"The result of {expr} is {res.content}."
        except Exception as exc:
            tool_success = False
            reply_text = f"Calculation failed: {exc}"

    # 12. File read
    elif intent == "file_read":
        tool_name = "file_read"
        fpath = params.get("path", "")
        try:
            from nivos.tools.file_read import FileReadTool

            ws_dir = get_workspace_dir()
            cfg_dir = get_workspace_dir().parent
            tool = FileReadTool(allowed_dirs=[str(ws_dir), str(cfg_dir), "."])
            res = tool.execute(path=fpath)
            tool_success = bool(res.success)
            reply_text = res.content
        except Exception as exc:
            tool_success = False
            reply_text = f"File read failed: {exc}"

    # Log to Activity (TraceStore)
    if trace_store is not None:
        try:
            import uuid

            ts = time.time()
            trace = Trace(
                trace_id=f"intent-{uuid.uuid4().hex[:12]}",
                query=user_query or match.intent,
                agent="intent-router",
                model="code-intent",
                engine="code",
                steps=[
                    TraceStep(
                        step_type=StepType.ROUTE,
                        timestamp=ts,
                        duration_seconds=0.01,
                        input={"intent": intent, "tool": tool_name},
                        output={"result": reply_text[:400]},
                        metadata={"matched_intent": intent},
                    )
                ],
                result=reply_text[:500],
                outcome="success" if tool_success else "failure",
                started_at=ts,
                ended_at=ts,
                total_latency_seconds=0.01,
                metadata={"intent": intent, "tool": tool_name or "none"},
            )
            trace_store.save(trace)
        except Exception as exc:
            logger.debug("Failed logging intent to TraceStore: %s", exc)

    return IntentExecutionResult(
        intent=intent,
        reply_text=reply_text,
        tool_name=tool_name,
        tool_success=tool_success,
        metadata=metadata,
    )

