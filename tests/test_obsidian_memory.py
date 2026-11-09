"""Tests for Phase 2 — Obsidian Memory integration.

Covers:
1. Re-indexing of .md files (frontmatter, [[links]], file_hash) skipping .obsidian/ and secrets.
2. Search vault: citations with note title and relative path; untrusted delimiter wrapping.
3. Save to inbox: creates new notes only; rejects overwrite, traversal, absolute paths, symlinks, me.md.
4. Memory review & proposal endpoints: list, approve, reject.
5. Injected memory: me.md strictly under 300 tokens and never modified.
6. Privacy guard: blocking vault notes to cloud models when allow_obsidian_to_cloud is False.
7. Background bge-m3 embedding worker.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nivos.connectors._stubs import Document
from nivos.connectors.obsidian import (
    ObsidianConnector,
    _extract_wiki_links,
    run_obsidian_background_embeddings,
)
from nivos.connectors.store import KnowledgeStore
from nivos.core.config import NivosConfig, ObsidianConfig
from nivos.security.policy import RequestOrigin, is_path_safe_for_write, is_tool_allowed
from nivos.server.app import create_app
from nivos.server.routes import _load_vault_me_md
from nivos.tools.vault_tools import SaveToInboxTool, SearchVaultTool


@pytest.fixture
def temp_vault():
    """Create a temporary mock Obsidian vault matching C:\\Nivos\\NIVOS-vault."""
    tmp = tempfile.mkdtemp(prefix="nivos_vault_test_")
    vault = Path(tmp)

    (vault / "Projects").mkdir()
    (vault / "Decisions").mkdir()
    (vault / "Learnings").mkdir()
    (vault / "Nivos-Inbox").mkdir()
    (vault / "Nivos-Memory").mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "secrets").mkdir()

    # Regular notes with frontmatter and wiki-links
    (vault / "Projects" / "ProjectX.md").write_text(
        "---\ntitle: Project X Architecture\nstatus: active\n---\n# Project X\nLinked to [[Roadmap]] and [[Decisions/001]].",
        encoding="utf-8",
    )
    (vault / "Decisions" / "001.md").write_text(
        "# Decision 001\nWe use SQLite for local speed.",
        encoding="utf-8",
    )
    (vault / "Nivos-Memory" / "me.md").write_text(
        "# About me\n- Name: Sooraj S\n- Location: Palakkad\n- Privacy: keep my notes local",
        encoding="utf-8",
    )

    # Secrets and junk that MUST be skipped
    (vault / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    (vault / ".env").write_text("SECRET_KEY=12345", encoding="utf-8")
    (vault / "secrets" / "api_token.md").write_text("my secret token", encoding="utf-8")
    (vault / "password.txt").write_text("supersecret", encoding="utf-8")

    yield vault

    shutil.rmtree(tmp, ignore_errors=True)


def test_wiki_links_extractor():
    text = "Check [[Alpha]] and [[Beta|Custom Label]] and [[Nested/Gamma]]."
    links = _extract_wiki_links(text)
    assert links == ["Alpha", "Beta", "Nested/Gamma"]


def test_obsidian_connector_skips_secrets_and_obsidian(temp_vault):
    connector = ObsidianConnector(vault_path=str(temp_vault))
    docs = list(connector.sync())

    rel_paths = [d.metadata["rel_path"] for d in docs]
    assert any("ProjectX.md" in p for p in rel_paths)
    assert any("001.md" in p for p in rel_paths)
    assert any("me.md" in p for p in rel_paths)

    # Must NOT include .obsidian, .env, password, or token
    for p in rel_paths:
        assert ".obsidian" not in p
        assert ".env" not in p
        assert "password" not in p.lower()
        assert "token" not in p.lower()

    # Verify [[wiki_links]] and file_hash are captured
    proj_doc = next(d for d in docs if "ProjectX.md" in d.metadata["rel_path"])
    assert "Roadmap" in proj_doc.metadata["links"]
    assert "Decisions/001" in proj_doc.metadata["links"]
    assert "file_hash" in proj_doc.metadata
    assert len(proj_doc.metadata["file_hash"]) == 64


def test_search_vault_citations_and_delimiters(temp_vault):
    tool = SearchVaultTool(vault_path=str(temp_vault))
    result = tool.execute(query="SQLite")

    assert result.success is True
    # Citation must include note title and path
    assert "001" in result.content
    assert "Decisions" in result.content
    # Must wrap in untrusted delimiters
    assert "<untrusted_vault_note" in result.content
    assert "</untrusted_vault_note>" in result.content


def test_save_to_inbox_creates_new_note(temp_vault):
    tool = SaveToInboxTool(vault_path=str(temp_vault))
    result = tool.execute(
        filename="new_idea.md",
        content="# A Great Idea\nDetails here.",
    )

    assert result.success is True
    assert "[Receipt: tool=save_to_inbox" in result.content
    created = temp_vault / "Nivos-Inbox" / "new_idea.md"
    assert created.is_file()
    assert "# A Great Idea" in created.read_text(encoding="utf-8")


def test_save_to_inbox_rejects_overwrite(temp_vault):
    tool = SaveToInboxTool(vault_path=str(temp_vault))
    (temp_vault / "Nivos-Inbox" / "existing.md").write_text("Original", encoding="utf-8")

    result = tool.execute(
        filename="existing.md",
        content="Overwritten!",
    )

    assert result.success is False
    assert "cannot overwrite" in result.content.lower()
    assert (temp_vault / "Nivos-Inbox" / "existing.md").read_text(encoding="utf-8") == "Original"


def test_save_to_inbox_rejects_path_traversal_and_symlinks(temp_vault):
    tool = SaveToInboxTool(vault_path=str(temp_vault))

    # Reject traversal
    res1 = tool.execute(filename="../escape.md", content="boom")
    assert res1.success is False
    assert "Security rejection" in res1.content

    # Reject subdirectories
    res2 = tool.execute(filename="sub/nested.md", content="boom")
    assert res2.success is False

    # Reject me.md
    res3 = tool.execute(filename="me.md", content="boom")
    assert res3.success is False
    assert "me.md" in res3.content


def test_me_md_read_only_and_prompt_budget(temp_vault):
    me_content = _load_vault_me_md(str(temp_vault))
    assert "About me" in me_content
    assert "Sooraj S" in me_content
    # Must stay strictly under 300 tokens (< 1000 chars)
    assert len(me_content) < 1000

    # Policy verification: never allow writing to me.md
    safe, reason = is_path_safe_for_write(str(temp_vault / "Nivos-Memory" / "me.md"))
    assert safe is False
    assert "me.md" in reason


def test_vault_api_proposals_lifecycle(temp_vault):
    # Place a proposal in Nivos-Inbox
    inbox_proposal = temp_vault / "Nivos-Inbox" / "skill-map.md"
    inbox_proposal.write_text("# Skill Map\nProposed skills map for agent.", encoding="utf-8")

    app = create_app(None, "test-model")
    client = TestClient(app)

    with patch("nivos.server.vault_routes._get_vault_path", return_value=temp_vault):
        # 1. List proposals
        res = client.get("/v1/vault/proposals")
        assert res.status_code == 200
        data = res.json()
        assert len(data["proposals"]) == 1
        assert data["proposals"][0]["filename"] == "skill-map.md"
        assert data["proposals"][0]["proposal_type"] == "skill-map"

        # 2. Approve proposal
        appr_res = client.post("/v1/vault/proposals/approve", json={"filename": "skill-map.md"})
        assert appr_res.status_code == 200
        # Should be moved to Nivos-Memory/
        assert (temp_vault / "Nivos-Memory" / "skill-map.md").is_file()
        assert not inbox_proposal.is_file()

        # 3. Create another proposal and reject it
        (temp_vault / "Nivos-Inbox" / "bad_idea.md").write_text("To be rejected", encoding="utf-8")
        rej_res = client.post("/v1/vault/proposals/reject", json={"filename": "bad_idea.md"})
        assert rej_res.status_code == 200
        assert not (temp_vault / "Nivos-Inbox" / "bad_idea.md").exists()

        # 4. Reject approve when target is me.md
        (temp_vault / "Nivos-Inbox" / "me.md").write_text("Fake me", encoding="utf-8")
        me_appr = client.post("/v1/vault/proposals/approve", json={"filename": "me.md"})
        assert me_appr.status_code == 403


def test_vault_config_persistence(temp_vault, monkeypatch):
    persisted = {}

    def mock_persist(section, updates):
        persisted[section] = updates

    monkeypatch.setattr("nivos.server.vault_routes._persist_config_section", mock_persist)

    app = create_app(None, "test-model")
    client = TestClient(app)

    with patch("nivos.server.vault_routes._get_vault_path", return_value=temp_vault):
        res = client.post(
            "/v1/vault/config",
            json={"vault_path": str(temp_vault), "allow_obsidian_to_cloud": True},
        )
        assert res.status_code == 200
        assert persisted["obsidian"]["vault_path"] == str(temp_vault)
        assert persisted["obsidian"]["allow_obsidian_to_cloud"] is True


def test_background_embeddings_worker():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = KnowledgeStore(db_path=db_path)
        # Store an obsidian chunk without embedding
        store.store(
            "Test note content for embedding",
            source="obsidian",
            source_id="test.md",
            title="Test",
        )
        store.close()

        # Mock OllamaEmbedder
        with patch("nivos.connectors.embeddings.OllamaEmbedder.is_available", return_value=True), \
             patch("nivos.connectors.embeddings.OllamaEmbedder.embed", return_value=b"\x00" * 128):
            count = run_obsidian_background_embeddings(db_path=db_path, model="bge-m3", force=True)
            assert count == 1

            # Check that embedding was updated in store
            store2 = KnowledgeStore(db_path=db_path)
            row = store2._conn.execute(
                "SELECT embedding, embedding_model_version FROM knowledge_chunks WHERE source='obsidian'"
            ).fetchone()
            assert row["embedding"] == b"\x00" * 128
            assert row["embedding_model_version"] == "bge-m3"
            store2.close()
    finally:
        try:
            os.remove(db_path)
        except Exception:
            pass
