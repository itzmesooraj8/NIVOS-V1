"""Tests for embeddings detection, repair CLI, and REST endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from nivos.cli.doctor_cmd import _check_embeddings_model
from nivos.connectors.embeddings import do_repair_embeddings
from nivos.core.config import NivosConfig
from nivos.server.app import create_app


def test_do_repair_embeddings_success(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return MagicMock(returncode=0, stdout="success", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda bin_name: "/usr/bin/ollama")

    ok, msg = do_repair_embeddings("nomic-embed-text")
    assert ok is True
    assert "Successfully repaired nomic-embed-text" in msg
    assert calls[0] == ["/usr/bin/ollama", "rm", "nomic-embed-text"]
    assert calls[1] == ["/usr/bin/ollama", "pull", "nomic-embed-text"]


def test_check_embeddings_model_detects_clean_and_missing() -> None:
    # 1. Missing model in list
    mock_engine = MagicMock(list_models=lambda: ["qwen3.5:4b", "qwen3.5:2b"])
    probe_missing = MagicMock(healthy=True, engine=mock_engine)
    res_missing = _check_embeddings_model({"ollama": probe_missing})
    assert res_missing.status == "warn"
    assert "not installed" in res_missing.message

    # 2. Healthy model
    mock_engine_ok = MagicMock(list_models=lambda: ["nomic-embed-text:latest", "qwen3.5:4b"])
    probe_ok = MagicMock(healthy=True, engine=mock_engine_ok)
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = MagicMock(status=200)
        mock_open.return_value.__enter__.return_value = mock_resp
        res_ok = _check_embeddings_model({"ollama": probe_ok})
        assert res_ok.status == "ok"
        assert "nomic-embed-text ready" in res_ok.message


def test_repair_embeddings_endpoint(monkeypatch) -> None:
    cfg = NivosConfig()
    cfg.traces.enabled = False
    app = create_app(MagicMock(health=lambda: True, list_models=lambda: []), "lazy", config=cfg)

    monkeypatch.setattr(
        "nivos.connectors.embeddings.do_repair_embeddings",
        lambda m="nomic-embed-text": (True, f"Successfully repaired {m}"),
    )

    client = TestClient(app)
    resp = client.post("/v1/models/repair-embeddings", json={"model": "nomic-embed-text"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "Successfully repaired nomic-embed-text" in data["message"]

