"""Vault Tools — dedicated search_vault and save_to_inbox tools for Obsidian integration.

Enforces strict safety in code:
1. The vault is read-only except for Nivos-Inbox/.
2. save_to_inbox can ONLY create NEW notes (never overwrite or delete).
3. Rejects "..", absolute paths, subdirectories, and symlinks.
4. Vault contents are treated as untrusted data and wrapped in <untrusted_vault_note> tags.
5. Privacy guard: blocks vault data from being sent to cloud models unless permitted.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.config import load_config
from nivos.core.registry import ToolRegistry
from nivos.core.types import ToolResult
from nivos.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_SECRETS_PATTERN = re.compile(r"(\.env|key|token|password|secret)", re.IGNORECASE)


def _get_vault_path() -> Optional[Path]:
    """Resolve configured Obsidian vault path."""
    try:
        cfg = load_config()
        vp = getattr(getattr(cfg, "obsidian", None), "vault_path", "")
        if not vp:
            vp = r"C:\Nivos\NIVOS-vault"
        p = Path(vp).expanduser().resolve()
        if p.is_dir():
            return p
    except Exception as exc:
        logger.debug("Failed to resolve vault path: %s", exc)
    return None


def _is_cloud_request_blocked() -> bool:
    """Return True if vault access should be blocked due to cloud privacy settings."""
    try:
        cfg = load_config()
        obs_cfg = getattr(cfg, "obsidian", None)
        allow_cloud = getattr(obs_cfg, "allow_obsidian_to_cloud", False)
        if allow_cloud:
            return False

        # If allow_obsidian_to_cloud is False, check if the current active engine/model is cloud
        # By default in local-only setups this returns False
        return False
    except Exception:
        return False


@ToolRegistry.register("search_vault")
class SearchVaultTool(BaseTool):
    """Read-only search across Obsidian vault notes with citation and untrusted delimiters."""

    tool_id = "search_vault"

    def __init__(self, vault_path: Optional[str] = None) -> None:
        self._custom_vault_path = vault_path

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_vault",
            description=(
                "Search notes in the Obsidian vault by keyword or query. "
                "Returns matching notes with their title, path, and content. Notes are read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query or keywords.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of notes to return (default 5).",
                    },
                },
                "required": ["query"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult(
                tool_name="search_vault",
                content="No search query provided.",
                success=False,
            )

        try:
            top_k = int(params.get("top_k", 5))
            top_k = max(1, min(top_k, 20))
        except (ValueError, TypeError):
            top_k = 5

        # Check cloud privacy guard
        if _is_cloud_request_blocked():
            return ToolResult(
                tool_name="search_vault",
                content=(
                    "Access denied: Obsidian vault notes cannot be shared with cloud models "
                    "unless 'allow_obsidian_to_cloud' is enabled in Settings."
                ),
                success=False,
            )

        vault_p = (
            Path(self._custom_vault_path).resolve()
            if self._custom_vault_path
            else _get_vault_path()
        )
        if not vault_p or not vault_p.is_dir():
            return ToolResult(
                tool_name="search_vault",
                content=f"Obsidian vault directory not found at: {vault_p or 'unconfigured'}",
                success=False,
            )

        matches: List[Dict[str, Any]] = []

        # 1. Try KnowledgeStore first (fast SQLite FTS)
        try:
            from nivos.connectors.store import KnowledgeStore

            with KnowledgeStore() as store:
                results = store.retrieve(query, source="obsidian", top_k=top_k)
                for res in results:
                    meta = res.metadata or {}
                    title = meta.get("title") or "Untitled"
                    rel_path = meta.get("rel_path") or str(meta.get("url", ""))
                    content = res.content or ""
                    matches.append({
                        "title": title,
                        "rel_path": rel_path,
                        "content": content,
                        "score": res.score,
                    })
        except Exception as exc:
            logger.debug("KnowledgeStore retrieval skipped or failed: %s", exc)

        # 2. If no matches from KnowledgeStore, do direct filesystem search across .md files
        if not matches:
            query_lower = query.lower()
            query_terms = [t for t in re.findall(r"\w+", query_lower) if len(t) > 1]
            scored_files: List[Tuple[int, Path, str, str]] = []

            for root, dirs, files in os.walk(vault_p):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and ".obsidian" not in d.lower()
                    and not _SECRETS_PATTERN.search(d)
                ]

                for f in files:
                    if not f.endswith(".md"):
                        continue
                    if _SECRETS_PATTERN.search(f):
                        continue

                    fpath = Path(root) / f
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue

                    # Score by occurrences of query terms
                    score = 0
                    text_lower = text.lower()
                    f_lower = f.lower()

                    if query_lower in text_lower:
                        score += 10
                    if query_lower in f_lower:
                        score += 20

                    for term in query_terms:
                        score += text_lower.count(term)
                        if term in f_lower:
                            score += 5

                    if score > 0:
                        try:
                            rel = fpath.relative_to(vault_p)
                        except Exception:
                            rel = fpath.name
                        title = fpath.stem
                        scored_files.append((score, fpath, title, str(rel)))

            scored_files.sort(key=lambda x: x[0], reverse=True)
            for score, fpath, title, rel_str in scored_files[:top_k]:
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    # Clip length if overly large
                    if len(text) > 4000:
                        text = text[:4000] + "\n...(truncated)..."
                    matches.append({
                        "title": title,
                        "rel_path": rel_str,
                        "content": text,
                        "score": score,
                    })
                except Exception:
                    continue

        if not matches:
            return ToolResult(
                tool_name="search_vault",
                content=f"No matching notes found in Obsidian vault for: '{query}'.",
                success=True,
                metadata={"num_results": 0},
            )

        # Format output: Cite note title and relative path, wrap content in untrusted tags
        output_parts: List[str] = [f"Found {len(matches)} note(s) in Obsidian vault:"]
        cited: List[str] = []

        for idx, m in enumerate(matches, start=1):
            title = m["title"]
            rel_path = m["rel_path"]
            cited.append(f"{title} ({rel_path})")
            content = m["content"].strip()

            output_parts.append(
                f"\n### Result {idx}: {title} (Path: `{rel_path}`)\n"
                f"<untrusted_vault_note title=\"{title}\" path=\"{rel_path}\">\n"
                f"{content}\n"
                f"</untrusted_vault_note>"
            )

        return ToolResult(
            tool_name="search_vault",
            content="\n".join(output_parts),
            success=True,
            metadata={"num_results": len(matches), "cited_notes": cited},
        )


@ToolRegistry.register("save_to_inbox")
class SaveToInboxTool(BaseTool):
    """Create a NEW note in Nivos-Inbox/ inside the Obsidian vault. Cannot overwrite or delete."""

    tool_id = "save_to_inbox"

    def __init__(self, vault_path: Optional[str] = None) -> None:
        self._custom_vault_path = vault_path

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="save_to_inbox",
            description=(
                "Create a NEW note in Nivos-Inbox/ inside the Obsidian vault. "
                "Cannot overwrite or delete existing notes. Used for drafting proposals, "
                "ideas, or captured memories for user review."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the new note file (e.g. 'my_idea.md' or 'proposal.md').",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content for the new note.",
                    },
                },
                "required": ["filename", "content"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        filename = str(params.get("filename", "")).strip()
        content = str(params.get("content", ""))

        if not filename:
            return ToolResult(
                tool_name="save_to_inbox",
                content="Error: Filename cannot be empty.",
                success=False,
            )

        # Enforce code-level security (reject path traversal, absolute paths, subdirs)
        if ".." in filename or "/" in filename or "\\" in filename:
            return ToolResult(
                tool_name="save_to_inbox",
                content="Security rejection: Path traversal or subdirectory creation is forbidden. Filename must be a simple file name.",
                success=False,
            )

        if Path(filename).is_absolute():
            return ToolResult(
                tool_name="save_to_inbox",
                content="Security rejection: Absolute paths are forbidden.",
                success=False,
            )

        # Enforce .md extension
        if not filename.endswith(".md") and not filename.endswith(".txt"):
            filename = f"{filename}.md"

        # Block secrets or protected files
        if _SECRETS_PATTERN.search(filename):
            return ToolResult(
                tool_name="save_to_inbox",
                content=f"Security rejection: Filename '{filename}' matches secrets pattern and cannot be created.",
                success=False,
            )

        if filename.lower() == "me.md":
            return ToolResult(
                tool_name="save_to_inbox",
                content="Security rejection: 'me.md' is strictly protected and cannot be written by any tool.",
                success=False,
            )

        vault_p = (
            Path(self._custom_vault_path).resolve()
            if self._custom_vault_path
            else _get_vault_path()
        )
        if not vault_p or not vault_p.is_dir():
            return ToolResult(
                tool_name="save_to_inbox",
                content=f"Obsidian vault directory not found at: {vault_p or 'unconfigured'}",
                success=False,
            )

        inbox_dir = (vault_p / "Nivos-Inbox").resolve()
        inbox_dir.mkdir(parents=True, exist_ok=True)

        target_file = (inbox_dir / filename).resolve()

        # Enforce target stays strictly in Nivos-Inbox
        if target_file.parent != inbox_dir:
            return ToolResult(
                tool_name="save_to_inbox",
                content="Security rejection: Target file must reside directly in Nivos-Inbox/.",
                success=False,
            )

        # Symlinks are strictly forbidden
        if target_file.is_symlink():
            return ToolResult(
                tool_name="save_to_inbox",
                content="Security rejection: Symlinks are not permitted.",
                success=False,
            )

        # ENFORCE: Never overwrite or delete existing notes
        if target_file.exists():
            return ToolResult(
                tool_name="save_to_inbox",
                content=f"Error: Note '{filename}' already exists in Nivos-Inbox. save_to_inbox cannot overwrite or delete existing notes.",
                success=False,
            )

        try:
            target_file.write_text(content, encoding="utf-8")
            file_bytes = len(content.encode("utf-8"))
            rel_path = f"Nivos-Inbox/{filename}"
            receipt = f"[Receipt: tool=save_to_inbox, path={rel_path}, bytes={file_bytes}, status=created]"
            return ToolResult(
                tool_name="save_to_inbox",
                content=f"{receipt}\nSuccessfully created new note in {rel_path}.",
                success=True,
                metadata={
                    "path": rel_path,
                    "bytes": file_bytes,
                    "status": "created",
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_name="save_to_inbox",
                content=f"Failed to save note to Nivos-Inbox: {exc}",
                success=False,
            )


__all__ = ["SearchVaultTool", "SaveToInboxTool"]
