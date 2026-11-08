"""Tests for workspace file upload and management routes."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nivos.server.app import create_app  # noqa: E402


@pytest.fixture
def workspace_client(tmp_path, monkeypatch):
    """Create a TestClient with workspace rooted in a temporary folder."""
    ws_dir = tmp_path / "test_workspace"
    ws_dir.mkdir()
    monkeypatch.setenv("NIVOS_WORKSPACE", str(ws_dir))

    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["test-model"]

    app = create_app(engine=engine, model="test-model")
    return TestClient(app), ws_dir


def test_upload_single_file(workspace_client):
    client, ws_dir = workspace_client
    file_content = b"print('hello workspace')"
    files = [("files", ("test.py", io.BytesIO(file_content), "text/x-python"))]

    resp = client.post("/v1/workspace/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["count"] == 1
    assert data["uploaded"][0]["path"] == "test.py"

    target = ws_dir / "test.py"
    assert target.exists()
    assert target.read_bytes() == file_content


def test_upload_folder_with_relative_paths(workspace_client):
    client, ws_dir = workspace_client
    f1 = ("files", ("index.js", io.BytesIO(b"console.log('hi');"), "text/javascript"))
    f2 = ("files", ("README.md", io.BytesIO(b"# My Project"), "text/markdown"))
    rel_paths = json.dumps(["my-project/src/index.js", "my-project/README.md"])

    resp = client.post(
        "/v1/workspace/upload",
        files=[f1, f2],
        data={"relative_paths": rel_paths},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2

    assert (ws_dir / "my-project" / "src" / "index.js").exists()
    assert (ws_dir / "my-project" / "README.md").exists()
    assert (ws_dir / "my-project" / "src" / "index.js").read_bytes() == b"console.log('hi');"


def test_upload_traversal_attack_rejected(workspace_client):
    client, ws_dir = workspace_client
    f1 = ("files", ("bad.txt", io.BytesIO(b"malicious"), "text/plain"))
    rel_paths = json.dumps(["../../bad.txt"])

    resp = client.post(
        "/v1/workspace/upload",
        files=[f1],
        data={"relative_paths": rel_paths},
    )
    assert resp.status_code == 400
    assert "outside workspace" in resp.json()["detail"].lower()


def test_list_and_delete_workspace_files(workspace_client):
    client, ws_dir = workspace_client
    (ws_dir / "sub").mkdir()
    (ws_dir / "sub" / "file.txt").write_text("sample")

    resp = client.get("/v1/workspace/files")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 2
    paths = [f["path"] for f in data["files"]]
    assert "sub" in paths
    assert "sub/file.txt" in paths

    del_resp = client.delete("/v1/workspace/files?path=sub/file.txt")
    assert del_resp.status_code == 200
    assert not (ws_dir / "sub" / "file.txt").exists()

