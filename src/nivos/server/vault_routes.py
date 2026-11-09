"""Vault API routes — Obsidian vault management, memory review proposals, and configuration.

Endpoints:
- GET  /v1/vault/config: Read current vault configuration and connection status.
- POST /v1/vault/config: Update vault configuration (vault_path, allow_obsidian_to_cloud).
- GET  /v1/vault/proposals: List pending proposals from Nivos-Inbox/ for memory review.
- POST /v1/vault/proposals/approve: Move approved proposal from Nivos-Inbox/ to Nivos-Memory/.
- POST /v1/vault/proposals/reject: Delete rejected proposal from Nivos-Inbox/.
- POST /v1/vault/sync: Trigger re-indexing of the vault into the knowledge store.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.core.config import load_config
from nivos.server.routes import _persist_config_section

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/vault", tags=["vault"])

_SECRETS_PATTERN = re.compile(r"(\.env|key|token|password|secret)", re.IGNORECASE)


def _get_vault_path() -> Path:
    cfg = load_config()
    vp = getattr(getattr(cfg, "obsidian", None), "vault_path", "")
    if not vp:
        vp = r"C:\Nivos\NIVOS-vault"
    return Path(vp).expanduser().resolve()


class VaultConfigUpdateReq(BaseModel):
    vault_path: Optional[str] = None
    allow_obsidian_to_cloud: Optional[bool] = None


class ProposalActionReq(BaseModel):
    filename: str = Field(..., description="Filename of the proposal in Nivos-Inbox/")


@router.get("/config")
async def get_vault_config(request: Request) -> Dict[str, Any]:
    """Return vault path, status, and cloud privacy settings."""
    cfg = load_config()
    obs_cfg = getattr(cfg, "obsidian", None)
    vp = getattr(obs_cfg, "vault_path", r"C:\Nivos\NIVOS-vault") if obs_cfg else r"C:\Nivos\NIVOS-vault"
    allow_cloud = getattr(obs_cfg, "allow_obsidian_to_cloud", False) if obs_cfg else False
    embed_model = getattr(obs_cfg, "embedding_model", "bge-m3") if obs_cfg else "bge-m3"

    v_path = Path(vp).expanduser().resolve()
    is_connected = v_path.is_dir()

    return {
        "vault_path": str(v_path),
        "connected": is_connected,
        "allow_obsidian_to_cloud": allow_cloud,
        "embedding_model": embed_model,
    }


@router.post("/config")
async def update_vault_config(req: VaultConfigUpdateReq, request: Request) -> Dict[str, Any]:
    """Update vault configuration and persist into config.toml."""
    updates: Dict[str, Any] = {}
    if req.vault_path is not None:
        updates["vault_path"] = req.vault_path
    if req.allow_obsidian_to_cloud is not None:
        updates["allow_obsidian_to_cloud"] = req.allow_obsidian_to_cloud

    if updates:
        try:
            _persist_config_section("obsidian", updates)
        except Exception as exc:
            logger.warning("Failed to persist vault config: %s", exc)

    return await get_vault_config(request)


@router.get("/proposals")
async def list_proposals(request: Request) -> Dict[str, Any]:
    """Scan Nivos-Inbox/ for pending proposals for the Memory Review screen."""
    vault_p = _get_vault_path()
    inbox_dir = vault_p / "Nivos-Inbox"

    if not inbox_dir.is_dir():
        return {"proposals": []}

    proposals: List[Dict[str, Any]] = []

    for item in sorted(inbox_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not item.is_file():
            continue
        if item.suffix.lower() not in (".md", ".txt", ".markdown"):
            continue
        if _SECRETS_PATTERN.search(item.name):
            continue

        try:
            content = item.read_text(encoding="utf-8", errors="replace")
            stat = item.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

            # Determine title and proposal type
            title = item.stem.replace("-", " ").replace("_", " ").title()
            for line in content.splitlines():
                line_str = line.strip()
                if line_str.startswith("# "):
                    title = line_str[2:].strip()
                    break

            ptype = "general"
            name_lower = item.name.lower()
            if "vault-map" in name_lower:
                ptype = "vault-map"
            elif "skill-map" in name_lower:
                ptype = "skill-map"
            elif "memory" in name_lower or "learning" in name_lower:
                ptype = "memory"

            proposals.append({
                "filename": item.name,
                "title": title,
                "content": content,
                "snippet": content[:300] + ("..." if len(content) > 300 else ""),
                "proposal_type": ptype,
                "mtime": mtime,
                "size_bytes": stat.st_size,
            })
        except Exception as exc:
            logger.debug("Failed reading proposal %s: %s", item.name, exc)
            continue

    return {"proposals": proposals}


@router.post("/proposals/approve")
async def approve_proposal(req: ProposalActionReq, request: Request) -> Dict[str, Any]:
    """Approve a proposal, moving it from Nivos-Inbox/ to Nivos-Memory/."""
    filename = req.filename.strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    # Invariant: me.md can never be overwritten
    if filename.lower() == "me.md":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: 'me.md' is strictly protected and cannot be overwritten.",
        )

    vault_p = _get_vault_path()
    inbox_file = (vault_p / "Nivos-Inbox" / filename).resolve()
    memory_dir = (vault_p / "Nivos-Memory").resolve()
    dest_file = (memory_dir / filename).resolve()

    if not inbox_file.is_file():
        raise HTTPException(status_code=404, detail=f"Proposal '{filename}' not found in Nivos-Inbox/.")

    memory_dir.mkdir(parents=True, exist_ok=True)

    # Double check destination isn't me.md
    if dest_file.name.lower() == "me.md":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Cannot overwrite 'me.md'.",
        )

    try:
        shutil.copy2(inbox_file, dest_file)
        inbox_file.unlink()
        logger.info("Approved proposal %s moved to Nivos-Memory/%s", filename, filename)
        return {
            "success": True,
            "message": f"Proposal '{filename}' approved and moved to Nivos-Memory/.",
            "destination": f"Nivos-Memory/{filename}",
        }
    except Exception as exc:
        logger.error("Failed to approve proposal %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=f"Failed to approve proposal: {exc}")


@router.post("/proposals/reject")
async def reject_proposal(req: ProposalActionReq, request: Request) -> Dict[str, Any]:
    """Reject a proposal, deleting it from Nivos-Inbox/."""
    filename = req.filename.strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    vault_p = _get_vault_path()
    inbox_file = (vault_p / "Nivos-Inbox" / filename).resolve()

    if not inbox_file.is_file():
        raise HTTPException(status_code=404, detail=f"Proposal '{filename}' not found in Nivos-Inbox/.")

    try:
        inbox_file.unlink()
        logger.info("Rejected proposal %s removed from Nivos-Inbox/", filename)
        return {
            "success": True,
            "message": f"Proposal '{filename}' rejected and removed from Nivos-Inbox/.",
        }
    except Exception as exc:
        logger.error("Failed to reject proposal %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=f"Failed to reject proposal: {exc}")


@router.post("/sync")
async def sync_vault(request: Request) -> Dict[str, Any]:
    """Trigger vault sync into knowledge store and kick off background embeddings."""
    vault_p = _get_vault_path()
    if not vault_p.is_dir():
        raise HTTPException(status_code=400, detail="Vault directory does not exist.")

    from nivos.connectors.obsidian import ObsidianConnector, trigger_obsidian_background_embedding
    from nivos.connectors.pipeline import IngestionPipeline
    from nivos.connectors.store import KnowledgeStore

    try:
        connector = ObsidianConnector(vault_path=str(vault_p))
        with KnowledgeStore() as store:
            pipeline = IngestionPipeline(store=store)
            synced = pipeline.ingest(connector.sync())

        # Trigger background bge-m3 embeddings
        trigger_obsidian_background_embedding(model="bge-m3")

        return {
            "success": True,
            "items_synced": synced,
            "message": f"Successfully indexed {synced} items from Obsidian vault.",
        }
    except Exception as exc:
        logger.error("Vault sync failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Vault sync failed: {exc}")
