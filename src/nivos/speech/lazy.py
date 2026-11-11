"""Lazy speech backend wrapper to prevent startup blocking."""

from __future__ import annotations

import logging
from typing import List, Optional

from nivos.core.config import NivosConfig
from nivos.speech._stubs import SpeechBackend, TranscriptionResult

logger = logging.getLogger(__name__)


class LazySpeechBackend(SpeechBackend):
    """Defers speech backend initialization until first use."""

    backend_id = "lazy_speech"

    def __init__(self, config: NivosConfig) -> None:
        self.config = config
        self._real: Optional[SpeechBackend] = None

    def _get_real(self) -> Optional[SpeechBackend]:
        if self._real is not None:
            return self._real
        from nivos.speech._discovery import get_speech_backend

        try:
            self._real = get_speech_backend(self.config)
            if self._real:
                self.backend_id = self._real.backend_id
        except Exception as exc:
            logger.debug("LazySpeechBackend resolution failed: %s", exc)
        return self._real

    def health(self) -> bool:
        real = self._get_real()
        return bool(real and real.health())

    def supported_formats(self) -> List[str]:
        real = self._get_real()
        if real:
            return real.supported_formats()
        return ["wav", "mp3", "m4a", "ogg", "flac"]

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        real = self._get_real()
        if not real:
            raise RuntimeError("No speech backend available.")
        return real.transcribe(audio, format=format, language=language)


__all__ = ["LazySpeechBackend"]

