"""Multi-engine wrapper — routes requests to the right backend by model name."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Dict, List

from openjarvis.core.types import Message
from openjarvis.engine._base import InferenceEngine
from openjarvis.engine._stubs import StreamChunk

logger = logging.getLogger(__name__)


class MultiEngine(InferenceEngine):
    """Wraps multiple engines and routes by model name.

    Models from each engine are discovered via ``list_models()``.
    When ``generate()`` or ``stream()`` is called, the model name
    is looked up to find which engine owns it.
    """

    engine_id = "multi"

    def __init__(self, engines: list[tuple[str, InferenceEngine]]) -> None:
        self._engines = engines
        self._model_map: Dict[str, InferenceEngine] = {}
        self._model_key_map: Dict[str, str] = {}
        self._refresh_map()

    def _refresh_map(self) -> None:
        self._model_map.clear()
        self._model_key_map.clear()
        # Process ollama first so Ollama models are strictly assigned to ollama
        engines_sorted = sorted(self._engines, key=lambda pair: 0 if pair[0] == "ollama" else 1)
        for key, engine in engines_sorted:
            try:
                for model_id in engine.list_models():
                    # Ollama models always go to Ollama
                    if model_id in self._model_map and self._model_key_map.get(model_id) == "ollama":
                        continue
                    self._model_map[model_id] = engine
                    self._model_key_map[model_id] = key
            except Exception as exc:
                logger.debug("Failed to list models for %s: %s", key, exc)

    _CLOUD_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "claude-", "gemini-", "openrouter/")

    def _engine_for(self, model: str) -> InferenceEngine:
        """Find the engine that owns a model, refreshing the map once if needed."""
        # 1. Ollama models always go to Ollama: check Ollama first if present
        for key, eng in self._engines:
            if key == "ollama":
                if self._model_key_map.get(model) == "ollama":
                    return eng
                # Auto-detect new pulls without restart
                try:
                    if model in eng.list_models():
                        self._model_map[model] = eng
                        self._model_key_map[model] = "ollama"
                        return eng
                except Exception:
                    pass

        # 2. Check if model is already mapped to an engine that listed it
        engine = self._model_map.get(model)
        if engine is not None:
            return engine

        # 3. Refresh and retry
        self._refresh_map()
        engine = self._model_map.get(model)
        if engine is not None:
            return engine

        # 4. Check again for Ollama newly pulled models
        for key, eng in self._engines:
            if key == "ollama":
                try:
                    if model in eng.list_models():
                        self._model_map[model] = eng
                        self._model_key_map[model] = "ollama"
                        return eng
                except Exception:
                    pass

        # 5. Cloud models:
        try:
            from openjarvis.server.cloud_router import is_cloud_model
            is_cloud = is_cloud_model(model)
        except Exception:
            is_cloud = False

        if is_cloud or any(model.startswith(p) for p in self._CLOUD_PREFIXES):
            for key, eng in self._engines:
                if key == "cloud":
                    if hasattr(eng, "can_serve") and not eng.can_serve(model):
                        raise ValueError(f"Add your key in Settings to use cloud model {model!r}.")
                    logger.info("Routing cloud model %r to cloud engine", model)
                    return eng
            raise ValueError(f"Add your key in Settings to use cloud model {model!r}.")

        raise ValueError(
            f"Model {model!r} not found in any engine "
            f"(known: {', '.join(sorted(self._model_map.keys())) or '<none>'}). "
            f"A model is served only by the engine that lists it."
        )

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self._engine_for(model).generate(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
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
        async for token in self._engine_for(model).stream(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield token

    async def stream_full(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator["StreamChunk"]:
        """Delegate stream_full() to the engine that owns the model."""
        engine = self._engine_for(model)
        async for chunk in engine.stream_full(messages, model=model, **kwargs):
            yield chunk

    def list_models(self) -> List[str]:
        self._refresh_map()
        return list(self._model_map.keys())

    def engine_key_for(self, model: str) -> str | None:
        """Return the registry key of the engine advertising *model*."""
        key = self._model_key_map.get(model)
        if key is not None:
            return key
        self._refresh_map()
        return self._model_key_map.get(model)

    def health(self) -> bool:
        return any(engine.health() for _key, engine in self._engines)

    def close(self) -> None:
        for _key, engine in self._engines:
            engine.close()


__all__ = ["MultiEngine"]
