"""Obsidian / Markdown vault connector.

Reads ``.md``, ``.markdown``, and ``.txt`` files from a local vault directory,
parses optional YAML frontmatter, and yields :class:`Document` objects that
can be ingested by the knowledge pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

from nivos.connectors._stubs import BaseConnector, Document, SyncStatus
from nivos.core.registry import ConnectorRegistry
from nivos.tools._stubs import ToolSpec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}

_SKIP_DIRS = {
    ".obsidian",
    ".git",
    ".trash",
    "__pycache__",
    "node_modules",
    ".venv",
}

# Secrets patterns to ignore: .env, *key*, *token*, *password*, *secret*
_SECRETS_PATTERN = re.compile(r"(\.env|key|token|password|secret)", re.IGNORECASE)


def _extract_wiki_links(text: str) -> List[str]:
    """Extract [[wiki_link]] targets from note content."""
    raw_links = re.findall(r"\[\[(.*?)\]\]", text)
    links = []
    for lk in raw_links:
        target = lk.split("|")[0].strip()
        if target and target not in links:
            links.append(target)
    return links

# ---------------------------------------------------------------------------
# Frontmatter parser (no PyYAML dependency)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter and return ``(metadata, body)``.

    Frontmatter is the block between two ``---`` lines at the very start of
    the file.  Only simple ``key: value`` and ``key: [a, b, c]`` syntax is
    handled — no nested YAML.
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing --- marker
    rest = text[3:]
    # Accept both \n--- and \r\n---
    end_marker = "\n---"
    end_idx = rest.find(end_marker)
    if end_idx == -1:
        return {}, text

    raw_fm = rest[:end_idx]
    # Body starts after the closing ---
    body_start = end_idx + len(end_marker)
    # Consume an optional trailing newline after the closing ---
    if body_start < len(rest) and rest[body_start] == "\n":
        body_start += 1
    body = rest[body_start:]

    metadata: Dict[str, Any] = {}
    for line in raw_fm.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue

        # List syntax:  [a, b, c]
        if raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1]
            items = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            metadata[key] = items
        else:
            # Strip optional surrounding quotes
            metadata[key] = raw_value.strip("'\"")

    return metadata, body


# ---------------------------------------------------------------------------
# ObsidianConnector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("obsidian")
class ObsidianConnector(BaseConnector):
    """Connector that reads a local Obsidian (or plain Markdown) vault.

    Parameters
    ----------
    vault_path:
        Absolute path to the vault root directory.  An empty string means
        "not yet configured" — :meth:`is_connected` will return ``False``.
    """

    connector_id = "obsidian"
    display_name = "Obsidian / Markdown"
    auth_type = "filesystem"

    def __init__(self, vault_path: str = "") -> None:
        if not vault_path:
            try:
                from nivos.core.config import load_config

                cfg = load_config()
                cfg_vault = getattr(getattr(cfg, "obsidian", None), "vault_path", "")
                if cfg_vault and Path(cfg_vault).is_dir():
                    vault_path = str(cfg_vault)
            except Exception:
                pass

        self._vault_path = vault_path
        self._connected: bool = bool(vault_path) and Path(vault_path).is_dir()
        self._items_synced: int = 0
        self._items_total: int = 0

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return ``True`` if vault_path is set and the directory exists."""
        return bool(self._vault_path) and Path(self._vault_path).is_dir()

    def disconnect(self) -> None:
        """Clear vault_path and mark as disconnected."""
        self._vault_path = ""
        self._connected = False
        try:
            from nivos.server.routes import _persist_config_section

            _persist_config_section("obsidian", {"vault_path": ""})
        except Exception:
            pass

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,  # noqa: ARG002 — unused but part of ABC
    ) -> Iterator[Document]:
        """Walk the vault and yield one :class:`Document` per text file.

        Parameters
        ----------
        since:
            If provided, skip files whose mtime is before this datetime.
        cursor:
            Not used for this filesystem connector (included for API
            compatibility).
        """
        vault = Path(self._vault_path)
        vault_name = vault.name

        collected_paths: List[Path] = []
        for root, dirs, files in os.walk(vault):
            # Prune hidden and known-junk directories in-place so os.walk
            # does not descend into them. Also skip secrets patterns and nested .obsidian.
            dirs[:] = [
                d
                for d in dirs
                if d not in _SKIP_DIRS
                and not d.startswith(".")
                and not _SECRETS_PATTERN.search(d)
                and ".obsidian" not in d.lower()
            ]

            for filename in files:
                fpath = Path(root) / filename
                if fpath.suffix.lower() not in _TEXT_EXTENSIONS:
                    continue
                # Skip secrets patterns: .env, *key*, *token*, *password*, *secret*
                if _SECRETS_PATTERN.search(filename):
                    continue
                collected_paths.append(fpath)

        self._items_total = len(collected_paths)
        synced = 0

        for fpath in collected_paths:
            # Apply since filter based on mtime
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
            if since is not None and mtime < since:
                continue

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            metadata, _body = _parse_frontmatter(text)

            title = metadata.get("title") or fpath.stem
            rel_path = fpath.relative_to(vault)

            url = (
                f"obsidian://open?vault={quote(vault_name)}&file={quote(str(rel_path))}"
            )

            # Extract [[wiki_links]] and sha256 file hash for incremental sync
            links = _extract_wiki_links(text)
            file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            doc_metadata = {k: v for k, v in metadata.items() if k != "title"}
            doc_metadata["links"] = links
            doc_metadata["file_hash"] = file_hash
            doc_metadata["rel_path"] = str(rel_path)

            doc = Document(
                doc_id=f"obsidian:{rel_path}",
                source="obsidian",
                doc_type="note",
                content=text,
                title=str(title),
                timestamp=mtime,
                url=url,
                metadata=doc_metadata,
            )
            synced += 1
            yield doc

        self._items_synced = synced
        if synced > 0:
            try:
                trigger_obsidian_background_embedding()
            except Exception as exc:
                logger.debug("Failed to trigger background embedding: %s", exc)

    def sync_status(self) -> SyncStatus:
        """Return sync progress from the most recent :meth:`sync` call."""
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            items_total=self._items_total,
        )

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose a single ``obsidian_search_notes`` tool for agent queries."""
        return [
            ToolSpec(
                name="obsidian_search_notes",
                description=(
                    "Search notes in the local Obsidian vault by keyword. "
                    "Returns matching note titles and snippets."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of results to return",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
                category="knowledge",
            )
        ]


logger = logging.getLogger(__name__)

_embedding_worker_running = False
_embedding_worker_lock = threading.Lock()


def run_obsidian_background_embeddings(
    db_path: str = "",
    model: str = "bge-m3",
    batch_size: int = 5,
    force: bool = False,
) -> int:
    """Scan knowledge_chunks for obsidian chunks without bge-m3 embeddings and embed them in low-priority background batches."""
    global _embedding_worker_running
    if not force:
        with _embedding_worker_lock:
            if _embedding_worker_running:
                return 0
            _embedding_worker_running = True

    try:
        from nivos.connectors.embeddings import OllamaEmbedder
        from nivos.connectors.store import KnowledgeStore

        embedder = OllamaEmbedder(model=model)
        if not embedder.is_available():
            logger.debug("Ollama embedder '%s' is not available; skipping background embedding.", model)
            return 0

        embedded_count = 0
        with KnowledgeStore(db_path=db_path) as store:
            rows = store._conn.execute(
                """
                SELECT id, content FROM knowledge_chunks
                WHERE source = 'obsidian' AND (embedding IS NULL OR embedding_model_version != ?)
                LIMIT 50
                """,
                (model,),
            ).fetchall()

            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                for r in batch:
                    chunk_id = r["id"]
                    text = r["content"]
                    emb = embedder.embed(text)
                    if emb is not None:
                        store._conn.execute(
                            """
                            UPDATE knowledge_chunks
                            SET embedding = ?, embedding_model_version = ?
                            WHERE id = ?
                            """,
                            (emb, model, chunk_id),
                        )
                        embedded_count += 1
                store._conn.commit()
                time.sleep(0.05)

        return embedded_count
    except Exception as exc:
        logger.debug("Obsidian background embedding encountered error: %s", exc)
        return 0
    finally:
        if not force:
            with _embedding_worker_lock:
                _embedding_worker_running = False


def trigger_obsidian_background_embedding(
    db_path: str = "",
    model: str = "bge-m3",
) -> None:
    """Start background embedding worker in a daemon thread."""
    t = threading.Thread(
        target=run_obsidian_background_embeddings,
        args=(db_path, model),
        daemon=True,
    )
    t.start()
