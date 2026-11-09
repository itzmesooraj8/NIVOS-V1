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
    r"\b(read|open|cat|view|inspect|examine|show|display|check|load)\b.*\b(file|document|code|notes?|log|contents?)\b|"
    r"\.(?:txt|py|md|json|csv|log|toml|yaml|yml|ts|tsx|js|jsx|html|css|ini|cfg)\b|"
    r"\b(read_file|read\s+file)\b",
    re.IGNORECASE,
)

_FILE_WRITE_PATTERN = re.compile(
    r"\b(write|save|create|append|edit|update|overwrite|dump)\b.*\b(file|document|note)\b|"
    r"\b(write_file|save_file|save\s+to\s+file|create\s+file)\b",
    re.IGNORECASE,
)

_SHELL_PATTERN = re.compile(
    r"\b(bash|sh|terminal|shell|command|exec|execute|run|pip\s+|npm\s+|git\s+|curl\s+|docker\s+|powershell|cmd\.exe)\b|"
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
    r"\b(schedule|remind|reminder|cron|timer|alarm|periodic|scheduled\s+task|cancel\s+task|list\s+tasks?|pause\s+task|resume\s+task)\b",
    re.IGNORECASE,
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

    # Web search: only when explicitly searching and not already pre-fetched
    if not has_prefetched_data and _WEB_SEARCH_PATTERN.search(clean_query):
        needed.append("web_search")

    # Weather: only when weather keywords present and not already pre-fetched
    if not has_prefetched_data and _WEATHER_PATTERN.search(clean_query):
        needed.append("get_weather")

    # File read
    if _FILE_READ_PATTERN.search(clean_query):
        needed.append("file_read")

    # File write
    if _FILE_WRITE_PATTERN.search(clean_query):
        needed.append("file_write")

    # Shell
    if _SHELL_PATTERN.search(clean_query):
        needed.append("sandboxed_shell")

    # Calculator
    if _CALCULATOR_PATTERN.search(clean_query):
        needed.append("calculator")

    # Scheduler
    if _SCHEDULER_PATTERN.search(clean_query):
        needed.extend(["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task"])

    if available_tools is not None:
        avail_set = set(available_tools)
        return [t for t in needed if t in avail_set]

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

        # 2. Code detection -> code tier
        if _CODE_PATTERN.search(clean_query):
            return self._code_model, "Code tier (qwen2.5-coder:7b): programming query", True

        # 3. Live search / weather pre-fetched data -> main tier
        if has_prefetched_data:
            return self._main_model, "Main tier (qwen3.5:4b): pre-fetched live data", False

        # 4. Casual greetings & simple chat -> fast tier (no tools, zero reasoning lag)
        if len(clean_query) < 80:
            if _GREETING_PATTERN.search(clean_query) or _SIMPLE_MATH_PATTERN.match(clean_query):
                return self._fast_model, "Fast tier (qwen3.5:2b): casual greeting / simple chat", False

        # 5. Default fallback -> main tier
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
    "warmup_dual_models",
    "warmup_fast_model",
]
