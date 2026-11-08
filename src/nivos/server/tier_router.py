"""Model tier routing and residency management for Nivos."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any, Optional, Tuple

from nivos.core.config import NivosConfig

logger = logging.getLogger(__name__)

# Code query indicator patterns
_CODE_PATTERN = re.compile(
    r"(```|`[^`]+`|\b(python|rust|javascript|typescript|c\+\+|golang|bash|html|css|sql|json|yaml)\b|"
    r"\b(def |class |fn |import |from [a-z0-9_]+ import|const |let |var |return |function\b|"
    r"regex|stack trace|syntax error|refactor|algorithm|debug this|write code|code to)\b)",
    re.IGNORECASE,
)

# Greeting and simple conversational opener patterns
_GREETING_PATTERN = re.compile(
    r"\b(hi|hello|hey|greetings|howdy|good\s+(morning|afternoon|evening|day)|"
    r"how\s+are\s+you|who\s+are\s+you|what\s+is\s+your\s+name|what\s+can\s+you\s+do|"
    r"tell\s+me\s+a\s+joke|thanks|thank\s+you|bye|goodbye|see\s+ya)\b",
    re.IGNORECASE,
)

# Simple math patterns that do not require complex reasoning
_SIMPLE_MATH_PATTERN = re.compile(
    r"^\s*(what\s+is\s+)?(\d+\s*[\+\-\*\/]\s*\d+)\s*\??\s*$",
    re.IGNORECASE,
)


class TierRouter:
    """Selects the best model tier based on query characteristics and machine constraints."""

    def __init__(self, config: Optional[NivosConfig] = None) -> None:
        self.config = config
        self._fast_model = getattr(getattr(config, "routing", None), "fast", "qwen3.5:2b") or "qwen3.5:2b"
        self._main_model = getattr(getattr(config, "routing", None), "main", "qwen3.5:4b") or "qwen3.5:4b"
        self._code_model = getattr(getattr(config, "routing", None), "code", "qwen2.5-coder:7b") or "qwen2.5-coder:7b"
        self._embeddings_model = getattr(getattr(config, "routing", None), "embeddings", "bge-m3:latest") or "bge-m3:latest"
        self._deep_model = getattr(getattr(config, "routing", None), "deep", "qwen3.5:9b") or "qwen3.5:9b"

    def route_query(
        self,
        query: str,
        requested_model: Optional[str] = None,
        *,
        has_prefetched_data: bool = False,
    ) -> Tuple[str, str, bool]:
        """Determine model, reason, and whether to send tool definitions.

        Returns:
            (model_id, routing_reason, should_send_tools)
        """
        clean_query = (query or "").strip()

        # 1. Manual model selection overrides router
        # If the user explicitly selected a model other than empty or auto
        if requested_model and requested_model.lower() not in ("", "auto", "router", "router (auto)"):
            if requested_model == self._deep_model or "9b" in requested_model.lower():
                return requested_model, f"Manual selection: deep tier ({requested_model})", True
            return requested_model, f"Manual override: {requested_model}", True

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
) -> bool:
    """Pre-load the fast model into memory at startup with keep_alive."""
    try:
        url = f"{host.rstrip('/')}/api/generate"
        payload = json.dumps({"model": model_name, "keep_alive": keep_alive}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug("Failed to warm up fast model '%s': %s", model_name, exc)
        return False


def enforce_resident_memory_budget(
    active_model: str,
    host: str = "http://localhost:11434",
    max_budget_gb: float = 6.0,
) -> None:
    """Ensure total resident chat models in Ollama do not exceed max_budget_gb.

    If loading active_model would exceed 6 GB (e.g. running 7B or 9B), unload
    other large models with keep_alive: 0.
    """
    try:
        ps_url = f"{host.rstrip('/')}/api/ps"
        with urllib.request.urlopen(ps_url, timeout=3) as resp:
            ps_data = json.loads(resp.read().decode())

        models = ps_data.get("models", [])
        total_size_gb = sum(m.get("size", 0) for m in models) / 1e9

        # If resident size exceeds budget, unload models other than active_model
        if total_size_gb > max_budget_gb:
            gen_url = f"{host.rstrip('/')}/api/generate"
            for m in models:
                m_name = m.get("name", "")
                if m_name and m_name != active_model and not m_name.startswith("bge-m3"):
                    unload_payload = json.dumps({"model": m_name, "keep_alive": 0}).encode()
                    req = urllib.request.Request(
                        gen_url,
                        data=unload_payload,
                        headers={"Content-Type": "application/json"},
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=2):
                            logger.info("Unloaded resident model '%s' to stay under 6 GB memory budget", m_name)
                    except Exception:
                        pass
    except Exception as exc:
        logger.debug("Memory budget enforcement failed: %s", exc)


__all__ = [
    "TierRouter",
    "enforce_resident_memory_budget",
    "warmup_fast_model",
]

