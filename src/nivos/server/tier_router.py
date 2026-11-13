"""Tiered Model Router and Memory Management for low-resource laptop environments."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.config import NivosConfig

logger = logging.getLogger(__name__)

# Patterns identifying programming code or coding intent
_CODE_PATTERN = re.compile(
    r"\b(def |class |fn |import |from [a-z0-9_]+ import|const |let |var |return |function\b|"
    r"```|SELECT |INSERT |UPDATE |DELETE |<div|public static void|async def |lambda |"
    r"curl |npm |pip |git |docker |kubectl |bash|powershell|try:|except |catch\(|#include)\b",
    re.IGNORECASE,
)

# Common greetings and simple casual questions
_GREETING_PATTERN = re.compile(
    r"\b(hi|hello|hey|howdy|sup|good morning|good afternoon|good evening|who are you|"
    r"what is your name|how are you|test|ping)\b",
    re.IGNORECASE,
)

# Simple arithmetic patterns (e.g. "what is 2 + 2", "12 * 8")
_SIMPLE_MATH_PATTERN = re.compile(
    r"^(?:what(?:'s|\s+is)?\s+)?-?\d+(?:\.\d+)?\s*[\+\-\*\/]\s*-?\d+(?:\.\d+)?\s*\??$",
    re.IGNORECASE,
)

# Tool relevance patterns for prompt diet
_WEB_SEARCH_PATTERN = re.compile(
    r"\b(search|google|web|online|lookup|look\s+up|browse|news|headlines?|latest|current\s+events|who\s+won|stock\s+price|scores?)\b",
    re.IGNORECASE,
)

_WEATHER_PATTERN = re.compile(
    r"\b(weather|temperature|forecast|rain|snow|humidity|wind|windy|storm|sunny|cloudy)\b",
    re.IGNORECASE,
)

_FILE_READ_PATTERN = re.compile(
    r"\b(read|open|cat|view|inspect|examine|show|display|check|load)\b"
    r".*\b(file|document|code|notes?|log|contents?|downloads?|desktop|folder|dir|directory)\b|"
    r"\.(?:txt|py|md|json|csv|log|toml|yaml|yml|ts|tsx|js|jsx|html|css|ini|cfg)\b|"
    r"\b(read_file|read\s+file)\b",
    re.IGNORECASE,
)

_FILE_WRITE_PATTERN = re.compile(
    r"\b(write|save|create|creat|mak|make|append|edit|update|overwrite|dump|put|download|donwload|downlaod|delete|remove|move)\b"
    r".*\b(file|document|note|text|code|script|folder|dir|directory|downloads?|desktop|workspace)\b|"
    r"\b(make\s+(?:a\s+)?file|create\s+(?:a\s+)?file|put\s+(?:this\s+)?in\s+(?:my\s+)?downloads?|"
    r"save\s+(?:to|in)\s+downloads?|write_file|save_file|create_file|save_to_file)\b",
    re.IGNORECASE,
)

_SHELL_PATTERN = re.compile(
    r"\b(bash|sh|terminal|shell|command|exec|execute|run|pip\s+|npm\s+|git\s+|curl\s+|docker\s+|powershell|cmd\.exe|install\b)\b|"
    r"```(?:bash|sh|shell|powershell|cmd)",
    re.IGNORECASE,
)

_CALCULATOR_PATTERN = re.compile(
    r"\b(calculate|calc|math|arithmetic|formula|equation)\b|"
    r"(?:sqrt|sin|cos|tan|log|pow)\s*\(|"
    r"\b\d+(?:\.\d+)?\s*[\+\-\*\/\^]\s*\d+",
    re.IGNORECASE,
)

_SCHEDULER_PATTERN = re.compile(
    r"\b(schedule|shedule|remind|reminder|cron|timer|alarm|periodic|scheduled\s+task|cancel\s+task|list\s+tasks?|pause\s+task|resume\s+task)\b",
    re.IGNORECASE,
)

_VAULT_SEARCH_PATTERN = re.compile(
    r"\b(vault|obsidian|second\s+brain|search\s+vault|find\s+in\s+vault|search\s+notes?)\b",
    re.IGNORECASE,
)

_VAULT_INBOX_PATTERN = re.compile(
    r"\b(save_to_inbox|save\s+to\s+inbox|add\s+to\s+inbox|inbox\s+note|new\s+vault\s+note|save\s+note\b.*?\binbox|take\s+a\s+note|save\s+note)\b",
    re.IGNORECASE,
)

_OPEN_APP_PATTERN = re.compile(
    r"\b(open\s+(?:the\s+)?app|launch\s+(?:the\s+)?app|open\s+app|launch\s+app|open\s+[a-zA-Z0-9_\-\:]+|launch\s+[a-zA-Z0-9_\-\:]+)\b",
    re.IGNORECASE,
)

_INSTALL_APP_PATTERN = re.compile(
    r"\b(install\s+app|install\s+application|winget\s+install|install\s+[A-Za-z0-9_\-\.]+)\b",
    re.IGNORECASE,
)

_TIME_TOOL_PATTERN = re.compile(
    r"\b(time|what\s+time|current\s+time|date|what\s+is\s+the\s+time|clock|what\s+time\s+is\s+it)\b",
    re.IGNORECASE,
)


def has_tool_intent(query: str) -> bool:
    """Check if a query indicates intent to use tools (files, shell, search, weather, etc.)."""
    if not query:
        return False
    clean = query.strip()
    return bool(
        _FILE_WRITE_PATTERN.search(clean)
        or _FILE_READ_PATTERN.search(clean)
        or _SHELL_PATTERN.search(clean)
        or _WEATHER_PATTERN.search(clean)
        or _WEB_SEARCH_PATTERN.search(clean)
        or _SCHEDULER_PATTERN.search(clean)
        or _CALCULATOR_PATTERN.search(clean)
        or _VAULT_SEARCH_PATTERN.search(clean)
        or _VAULT_INBOX_PATTERN.search(clean)
        or _OPEN_APP_PATTERN.search(clean)
        or _INSTALL_APP_PATTERN.search(clean)
        or _TIME_TOOL_PATTERN.search(clean)
    )


def filter_relevant_tools(
    query: str,
    available_tools: Optional[List[str]] = None,
    has_prefetched_data: bool = False,
) -> List[str]:
    """Filter tools down to only those relevant to the query to avoid prompt bloating.

    Returns an empty list for plain conversation / reasoning, avoiding ~1,500 prompt
    tokens of tool schema overhead on CPU.
    """
    clean_query = query.strip() if query else ""
    if not clean_query:
        return []

    needed: List[str] = []

    # Time tool: instant time inquiry
    if _TIME_TOOL_PATTERN.search(clean_query):
        needed.append("get_current_time")

    # Web search
    if _WEB_SEARCH_PATTERN.search(clean_query):
        needed.append("web_search")

    # Weather
    if _WEATHER_PATTERN.search(clean_query):
        needed.append("get_weather")

    # File read
    if _FILE_READ_PATTERN.search(clean_query):
        needed.append("file_read")

    # Vault inbox
    is_inbox = bool(_VAULT_INBOX_PATTERN.search(clean_query))
    if is_inbox:
        needed.append("save_to_inbox")

    # File write (only if not an inbox note saving query, or if explicit file keywords exist)
    if _FILE_WRITE_PATTERN.search(clean_query):
        has_file_kw = any(k in clean_query.lower() for k in ["workspace", ".txt", ".json", ".py", "directory", "folder", "script"])
        if not is_inbox or has_file_kw:
            needed.append("file_write")

    # Shell: strictly forbid for scheduled task queries unless explicit shell command keywords are used
    if _SHELL_PATTERN.search(clean_query):
        clean_lower = clean_query.lower()
        is_task_query = "scheduled task" in clean_lower or "schedule task" in clean_lower or "scheduled_task" in clean_lower
        has_explicit_shell = any(k in clean_lower for k in ["bash", "sh ", "powershell", "cmd.exe", "pip ", "npm ", "docker "])
        if not is_task_query or has_explicit_shell:
            needed.append("sandboxed_shell")

    # Calculator
    if _CALCULATOR_PATTERN.search(clean_query):
        needed.append("calculator")

    # Scheduler (never use sandboxed_shell for scheduled tasks)
    if _SCHEDULER_PATTERN.search(clean_query):
        needed.append("list_scheduled_tasks")
        if re.search(r"\b(cancel|delete|stop|remove)\b", clean_query, re.IGNORECASE):
            needed.append("cancel_scheduled_task")
        if re.search(r"\b(schedule|create|add|new|remind|set\s+timer)\b", clean_query, re.IGNORECASE):
            needed.append("schedule_task")

    # Vault search
    if _VAULT_SEARCH_PATTERN.search(clean_query):
        needed.append("search_vault")


    # Open app
    if _OPEN_APP_PATTERN.search(clean_query):
        needed.append("open_app")

    # Install app
    if _INSTALL_APP_PATTERN.search(clean_query):
        needed.append("install_app")

    if available_tools is not None:
        avail_set = set(available_tools)
        return [t for t in needed if t in avail_set]

    return needed

    return needed


def estimate_token_count(text: str) -> int:
    """Fast approximation of token count for text (~3.5 chars per token)."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


def compute_prompt_token_breakdown(
    *,
    system_prompt: str = "",
    tools: Optional[List[Any]] = None,
    memory_text: str = "",
    clock_text: str = "",
    history_messages: Optional[List[Any]] = None,
    user_query: str = "",
) -> Dict[str, Any]:
    """Compute token breakdown across all components of the prompt."""
    sys_tok = estimate_token_count(system_prompt)

    tools_tok = 0
    tools_count = 0
    if tools:
        tools_count = len(tools)
        try:
            tools_json = json.dumps(tools)
        except Exception:
            tools_json = str(tools)
        tools_tok = estimate_token_count(tools_json)

    mem_tok = estimate_token_count(memory_text)
    clock_tok = estimate_token_count(clock_text)

    hist_tok = 0
    if history_messages:
        for m in history_messages:
            content = getattr(m, "content", "") or ""
            hist_tok += estimate_token_count(content)

    query_tok = estimate_token_count(user_query)
    total = sys_tok + tools_tok + mem_tok + clock_tok + hist_tok + query_tok

    return {
        "system_prompt": sys_tok,
        "tools": tools_tok,
        "tools_count": tools_count,
        "memory": mem_tok,
        "clock": clock_tok,
        "history": hist_tok,
        "user_query": query_tok,
        "total": total,
    }


class TierRouter:
    """Routes queries to appropriate model tiers based on complexity and resource budget."""

    def __init__(self, config: Optional[NivosConfig] = None) -> None:
        self.config = config
        routing_cfg = getattr(config, "routing", None)
        self._fast_model = getattr(routing_cfg, "fast", "qwen3.5:2b") if routing_cfg else "qwen3.5:2b"
        self._main_model = getattr(routing_cfg, "main", "qwen3.5:4b") if routing_cfg else "qwen3.5:4b"
        self._code_model = getattr(routing_cfg, "code", "qwen2.5-coder:7b") if routing_cfg else "qwen2.5-coder:7b"
        self._deep_model = getattr(routing_cfg, "deep", "qwen3.5:9b") if routing_cfg else "qwen3.5:9b"

    def route_query(
        self,
        query: str,
        requested_model: Optional[str] = None,
        available_models: Optional[List[str]] = None,
        has_prefetched_data: bool = False,
    ) -> Tuple[str, str, bool]:
        """Route a user query to the best model tier.

        Returns:
            (selected_model_name, routing_reason, allows_escalation)
        """
        # 1. Manual user override takes precedence
        if requested_model and requested_model not in {"auto", "default", ""}:
            reason = f"Manual override: {requested_model}"
            if requested_model == self._deep_model:
                reason = f"Manual override: deep tier ({requested_model})"
            return requested_model, reason, True

        clean_query = query.strip() if query else ""

        # 2. Tool intent -> main tier (never fast) with tools enabled
        # Crucial: Check tool intent before greetings so queries like
        # "create a file called test-nivos.txt in my downloads containing the word hello"
        # are never misclassified as casual greetings.
        if has_tool_intent(clean_query):
            if _CODE_PATTERN.search(clean_query) and "def " in clean_query:
                return self._code_model, "Code tier (qwen2.5-coder:7b): programming query with tool intent", True
            return self._main_model, "Main tier (qwen3.5:4b): tool intent detected", True

        # 3. Code detection -> code tier
        if _CODE_PATTERN.search(clean_query):
            return self._code_model, "Code tier (qwen2.5-coder:7b): programming query", True

        # 4. Live search / weather pre-fetched data -> main tier
        if has_prefetched_data:
            return self._main_model, "Main tier (qwen3.5:4b): pre-fetched live data", False

        # 5. Casual greetings & simple chat -> fast tier (no tools, zero reasoning lag)
        if len(clean_query) < 80 and not has_tool_intent(clean_query):
            if _GREETING_PATTERN.search(clean_query) or _SIMPLE_MATH_PATTERN.match(clean_query):
                return self._fast_model, "Fast tier (qwen3.5:2b): casual greeting / simple chat", False

        # 6. Default fallback -> main tier
        return self._main_model, "Main tier (qwen3.5:4b): general assistant", True


def warmup_fast_model(
    model_name: str = "qwen3.5:2b",
    host: str = "http://localhost:11434",
    keep_alive: str = "30m",
    num_ctx: int = 4096,
) -> bool:
    """Pre-load a model into memory with a 4k context window and keep_alive."""
    try:
        url = f"{host.rstrip('/')}/api/generate"
        payload = json.dumps({
            "model": model_name,
            "keep_alive": keep_alive,
            "options": {"num_ctx": num_ctx},
        }).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug("Failed to warm up model '%s': %s", model_name, exc)
        return False


def warmup_dual_models(
    fast_model: str = "qwen3.5:2b",
    main_model: str = "qwen3.5:4b",
    host: str = "http://localhost:11434",
    keep_alive: str = "30m",
    num_ctx: int = 4096,
) -> None:
    """Warm up both the fast and main models into memory so switching is instant."""
    warmup_fast_model(fast_model, host=host, keep_alive=keep_alive, num_ctx=num_ctx)
    warmup_fast_model(main_model, host=host, keep_alive=keep_alive, num_ctx=num_ctx)


def enforce_resident_memory_budget(
    active_model: str,
    host: str = "http://localhost:11434",
    max_budget_gb: float = 6.5,
    fast_model: str = "qwen3.5:2b",
    main_model: str = "qwen3.5:4b",
) -> None:
    """Ensure total resident chat models in Ollama do not exceed max_budget_gb.

    The primary working pair (fast_model and main_model) combined size with
    an 8k context window is ~5.8 GB, fitting comfortably within the memory cap.
    Neither fast_model nor main_model will evict the other when active.
    Only heavy models (such as 7B coder or 9B deep) are evicted to stay within budget.
    """
    try:
        ps_url = f"{host.rstrip('/')}/api/ps"
        with urllib.request.urlopen(ps_url, timeout=3) as resp:
            ps_data = json.loads(resp.read().decode())

        models = ps_data.get("models", [])
        total_size_gb = sum(m.get("size", 0) for m in models) / 1e9

        if total_size_gb > max_budget_gb:
            gen_url = f"{host.rstrip('/')}/api/generate"
            for m in models:
                m_name = m.get("name", "")
                if not m_name:
                    continue
                # If active model is in the fast/main pair, preserve both fast and main
                if active_model in (fast_model, main_model) and m_name in (fast_model, main_model):
                    continue
                if m_name == active_model or m_name.startswith("bge-m3"):
                    continue

                unload_payload = json.dumps({"model": m_name, "keep_alive": 0}).encode()
                req = urllib.request.Request(
                    gen_url,
                    data=unload_payload,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=2):
                        logger.info("Unloaded resident model '%s' to stay under %.1f GB memory budget", m_name, max_budget_gb)
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Memory budget enforcement failed: %s", exc)


__all__ = [
    "TierRouter",
    "compute_prompt_token_breakdown",
    "enforce_resident_memory_budget",
    "estimate_token_count",
    "filter_relevant_tools",
    "has_tool_intent",
    "warmup_dual_models",
    "warmup_fast_model",
]
