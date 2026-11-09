"""Sandbox routes — status, reset, and session auto-approval for Docker sandbox."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.sandbox.docker_manager import (
    get_sandbox_auto_approve,
    get_sandbox_status_info,
    reset_sandbox,
    set_sandbox_auto_approve,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])


class AutoApproveRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable auto-approval inside Docker sandbox for this session.")


@router.get("/status")
async def get_sandbox_status(request: Request) -> Dict[str, Any]:
    """Return status of Docker sandbox, container lifecycle, and setup guidance."""
    return get_sandbox_status_info()


@router.post("/reset")
async def reset_sandbox_endpoint(request: Request) -> Dict[str, Any]:
    """Force remove and reset the Docker sandbox container."""
    success, msg = reset_sandbox()
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}


@router.post("/auto-approve")
async def update_auto_approve(req: AutoApproveRequest, request: Request) -> Dict[str, Any]:
    """Toggle per-session auto-approval inside the Docker sandbox. Never applies to host."""
    res = set_sandbox_auto_approve(req.enabled)
    return {"auto_approve_session": res}

