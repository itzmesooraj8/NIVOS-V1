"""Checkpoint management routes — pre-mutation snapshot inspection and undo."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nivos.storage.checkpoint_manager import (
    get_latest_checkpoint,
    list_checkpoints,
    undo_checkpoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/checkpoints", tags=["checkpoints"])


class UndoRequest(BaseModel):
    checkpoint_id: Optional[str] = None


@router.get("")
async def get_checkpoints(limit: int = Query(50, ge=1, le=100)) -> Dict[str, Any]:
    """List recent file mutation checkpoints (newest first)."""
    cps = list_checkpoints(limit=limit)
    return {"checkpoints": cps, "count": len(cps)}


@router.get("/latest")
async def get_latest() -> Dict[str, Any]:
    """Retrieve the most recent checkpoint snapshot."""
    cp = get_latest_checkpoint()
    return {"latest": cp}


@router.post("/undo")
async def undo_latest_or_target(req: Optional[UndoRequest] = None) -> Dict[str, Any]:
    """Revert the last file mutation or a specific checkpoint."""
    cid = req.checkpoint_id if req else None
    success, message, cp = undo_checkpoint(checkpoint_id=cid)
    if not success and not cp:
        # If there are no checkpoints to undo
        return {"success": False, "message": message, "checkpoint": None}
    return {
        "success": success,
        "message": message,
        "checkpoint": cp,
    }

