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
    num_ctx: int = 8192,
) -> bool:
    """Pre-load a model into memory with an 8k context window and keep_alive."""
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
    num_ctx: int = 8192,
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
    "enforce_resident_memory_budget",
    "warmup_dual_models",
    "warmup_fast_model",
]
