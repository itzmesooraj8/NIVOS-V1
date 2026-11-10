"""Tests for Phase 4: File mutation checkpoints, snapshot retention, undo, and receipts."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from nivos.storage.checkpoint_manager import (
    create_checkpoint,
    get_latest_checkpoint,
    list_checkpoints,
    undo_checkpoint,
    get_checkpoint_dir,
)
from nivos.server.app import create_app
from nivos.core.registry import ToolRegistry


def test_checkpoint_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    test_file = tmp_path / "hello.txt"
    test_file.write_text("initial content", encoding="utf-8")

    cp = create_checkpoint(test_file, operation="write")
    assert cp["existed_before"] is True
    assert cp["file_path"] == str(test_file.resolve())
    assert cp["backup_file"] is not None
    assert (tmp_path / "checkpoints" / cp["backup_file"]).read_text(encoding="utf-8") == "initial content"
    assert "status=snapshot_taken" in cp["receipt"]

    # Modify file
    test_file.write_text("modified content", encoding="utf-8")

    # Undo
    ok, msg, undone = undo_checkpoint(cp["id"])
    assert ok is True
    assert test_file.read_text(encoding="utf-8") == "initial content"
    assert not (tmp_path / "checkpoints" / cp["backup_file"]).exists()
    assert "status=undone" in undone["receipt"]


def test_checkpoint_new_file_undo_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    new_file = tmp_path / "brand_new.txt"
    assert not new_file.exists()

    cp = create_checkpoint(new_file, operation="write")
    assert cp["existed_before"] is False
    assert cp["backup_file"] is None

    # Simulate write
    new_file.write_text("created by agent", encoding="utf-8")
    assert new_file.exists()

    # Undo
    ok, msg, undone = undo_checkpoint()
    assert ok is True
    assert not new_file.exists()
    assert "status=undone" in undone["receipt"]


def test_checkpoint_retention_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)

    # Create 55 checkpoints with backup files
    files = []
    for i in range(55):
        f = tmp_path / f"file_{i}.txt"
        f.write_text(f"content {i}", encoding="utf-8")
        cp = create_checkpoint(f, operation="write")
        files.append((f, cp))

    cps = list_checkpoints(limit=100)
    assert len(cps) == 50

    # The first 5 should be pruned
    cp_dir = tmp_path / "checkpoints"
    for i in range(5):
        old_bk = files[i][1]["backup_file"]
        assert not (cp_dir / old_bk).exists(), f"Old backup {old_bk} should be pruned"

    # Checkpoints 5..54 should exist
    for i in range(5, 55):
        bk = files[i][1]["backup_file"]
        assert (cp_dir / bk).exists()


def test_checkpoint_api_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    app = create_app(engine="ollama", model="qwen3.5:2b")
    client = TestClient(app)

    # Initially empty
    res = client.get("/v1/checkpoints")
    assert res.status_code == 200
    assert res.json()["checkpoints"] == []

    res = client.get("/v1/checkpoints/latest")
    assert res.status_code == 200
    assert res.json()["latest"] is None

    # Create a test file and checkpoint
    test_file = tmp_path / "sample.py"
    test_file.write_text("print('before')", encoding="utf-8")
    cp = create_checkpoint(test_file, operation="patch")

    # Mutate
    test_file.write_text("print('after')", encoding="utf-8")

    res = client.get("/v1/checkpoints/latest")
    assert res.status_code == 200
    latest = res.json()["latest"]
    assert latest is not None
    assert latest["id"] == cp["id"]

    # Undo via API
    res = client.post("/v1/checkpoints/undo", json={"checkpoint_id": cp["id"]})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert test_file.read_text(encoding="utf-8") == "print('before')"


def test_file_write_emits_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib
    import nivos.tools.file_write as fw_mod
    importlib.reload(fw_mod)

    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    tool = fw_mod.FileWriteTool(allowed_dirs=[str(tmp_path)])
    target = tmp_path / "written.txt"

    res = tool.execute(path=str(target), content="first version")
    assert res.success is True
    assert "checkpoint_id" in res.metadata
    assert "status=snapshot_taken" in res.content

    # Write again
    res2 = tool.execute(path=str(target), content="second version")
    assert res2.success is True
    assert "checkpoint_id" in res2.metadata

    # Undo restores "first version"
    ok, _, _ = undo_checkpoint()
    assert ok is True
    assert target.read_text(encoding="utf-8") == "first version"


def test_apply_patch_emits_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib
    import nivos.tools.apply_patch as ap_mod
    importlib.reload(ap_mod)

    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    target = tmp_path / "code.py"
    target.write_text("line1\nline2\n", encoding="utf-8")

    tool = ap_mod.ApplyPatchTool()
    patch = (
        f"--- a/{target.name}\n"
        f"+++ b/{target.name}\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_modified\n"
    )

    res = tool.execute(patch=patch, path=str(target))
    assert res.success is True
    assert "checkpoint_id" in res.metadata
    assert target.read_text(encoding="utf-8") == "line1\nline2_modified\n"

    # Undo restores "line1\nline2\n"
    ok, _, _ = undo_checkpoint()
    assert ok is True
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_save_to_inbox_emits_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib
    import nivos.tools.vault_tools as vt_mod
    importlib.reload(vt_mod)

    monkeypatch.setattr("nivos.storage.checkpoint_manager.get_config_dir", lambda: tmp_path)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    tool = vt_mod.SaveToInboxTool(vault_path=str(vault_dir))

    res = tool.execute(filename="my_note.md", content="# Note Title\nContent here")
    assert res.success is True
    assert "checkpoint_id" in res.metadata
    assert "status=snapshot_taken" in res.content

    note_path = vault_dir / "Nivos-Inbox" / "my_note.md"
    assert note_path.exists()

    # Undo deletes note
    ok, _, _ = undo_checkpoint()
    assert ok is True
    assert not note_path.exists()

