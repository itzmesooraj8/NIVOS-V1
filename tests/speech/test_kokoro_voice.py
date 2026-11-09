"""Tests for Phase 6 Kokoro voice configuration, diagnostics, and graceful fallback."""

import pytest
from unittest.mock import MagicMock, patch

from nivos.core.config import SpeechConfig, NivosConfig
from nivos.speech._tts_discovery import voice_preferences, default_voice_for
from nivos.speech.kokoro_tts import KokoroTTSBackend


def test_speech_config_defaults():
    """Verify that SpeechConfig defaults to kokoro with British male voice and read_aloud=False."""
    cfg = SpeechConfig()
    assert cfg.tts_backend == "kokoro"
    assert cfg.voice_id == "bm_george"
    assert cfg.read_aloud is False
    assert cfg.voice_speed == 1.0


def test_kokoro_default_voices():
    """Verify default voices and language resolution for Kokoro."""
    assert default_voice_for("kokoro") == "bm_george"
    backend = KokoroTTSBackend()
    voices = backend.available_voices()
    assert "bm_george" in voices
    assert "bm_lewis" in voices
    assert "bf_emma" in voices
    assert "af_heart" in voices


def test_windows_blocked_component_error_message(monkeypatch):
    """Verify that when Windows Smart App Control or security blocks DLLs, clear diagnostic is given."""
    backend = KokoroTTSBackend()

    def mock_kmodel(**kwargs):
        raise RuntimeError("LoadLibrary failed: Access is denied. Smart App Control blocked DLL")

    mock_kokoro = MagicMock()
    mock_kokoro.KModel = mock_kmodel

    with patch("nivos.speech.kokoro_tts._import_kokoro", return_value=mock_kokoro):
        with pytest.raises(RuntimeError) as excinfo:
            backend._ensure_model()
        err_msg = str(excinfo.value)
        assert "Windows Smart App Control or security software blocked a component" in err_msg


def test_kokoro_huggingface_download_error(monkeypatch):
    """Verify that model download failures provide actionable error."""
    backend = KokoroTTSBackend()

    def mock_kmodel(**kwargs):
        raise ConnectionError("Failed to connect to huggingface.co")

    mock_kokoro = MagicMock()
    mock_kokoro.KModel = mock_kmodel

    with patch("nivos.speech.kokoro_tts._import_kokoro", return_value=mock_kokoro):
        with pytest.raises(RuntimeError) as excinfo:
            backend._ensure_model()
        err_msg = str(excinfo.value)
        assert "Failed to download Kokoro TTS model weights" in err_msg
