"""LazyEngine — Resilient placeholder engine that defers connection until available.

Allows the Nivos server to boot and bind HTTP ports instantly even when local or
cloud inference engines are temporarily stopped or unreachable.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.types import Message
from nivos.engine._base import EngineConnectionError, InferenceEngine
from nivos.engine._stubs import StreamChunk

logger = logging.getLogger(__name__)


class LazyEngine(InferenceEngine):
    """Placeholder engine that tries to resolve a real backend dynamically."""

    engine_id = "lazy"
    is_cloud = False
    is_lazy = True

    def __init__(self, config: Any, engine_key: str | None = None) -> None:
        self.config = config
        self.engine_key = engine_key
        self._real_engine: Optional[InferenceEngine] = None
        self._real_key: Optional[str] = None
        self._last_check: float = 0.0
        self._check_cooldown: float = 5.0

    def _try_resolve(self) -> Optional[Tuple[str, InferenceEngine]]:
        import time

        now = time.monotonic()
        if self._real_engine is not None:
            try:
                if self._real_engine.health():
                    return self._real_key, self._real_engine
            except Exception:
                self._real_engine = None
                self._real_key = None

        if now - self._last_check < self._check_cooldown:
            return None
        self._last_check = now

        from nivos.engine._discovery import get_engine

        try:
            res = get_engine(self.config, self.engine_key, fast=True)
            if res is not None:
                self._real_key, self._real_engine = res
                return res
        except Exception as exc:
            logger.debug("LazyEngine resolution attempt failed: %s", exc)

        return None

    def health(self) -> bool:
        """Return True only if an underlying engine is currently healthy."""
        res = self._try_resolve()
        if res is not None:
            try:
                return bool(res[1].health())
            except Exception:
                return False
        return False

    def list_models(self) -> List[str]:
        """List models if underlying engine is reachable, otherwise empty."""
        res = self._try_resolve()
        if res is not None:
            try:
                return res[1].list_models()
            except Exception:
                return []
        return []

    def can_serve(self, model: str) -> bool:
        res = self._try_resolve()
        if res is not None:
            try:
                return res[1].can_serve(model)
            except Exception:
                return False
        return False

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        res = self._try_resolve()
        if res is not None:
            return res[1].generate(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        raise EngineConnectionError(
            "No inference engine is currently reachable. "
            "Please start Ollama (run `ollama serve`) or configure a cloud API key in Settings."
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        res = self._try_resolve()
        if res is not None:
            async for token in res[1].stream(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield token
            return

        yield (
            "No inference engine is currently reachable. "
            "Please start Ollama (run `ollama serve`) or configure a cloud API key in Settings."
        )

    async def stream_full(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        res = self._try_resolve()
        if res is not None:
            async for chunk in res[1].stream_full(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield chunk
            return

        yield StreamChunk(
            content=(
                "No inference engine is currently reachable. "
                "Please start Ollama (run `ollama serve`) or configure a cloud API key in Settings."
            )
        )
        yield StreamChunk(finish_reason="stop")


__all__ = ["LazyEngine"]

