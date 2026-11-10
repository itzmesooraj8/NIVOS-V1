"""Sandbox routes — status, reset, package persistence, permissions, and Docker daemon lifecycle."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.core.config import load_config
from nivos.sandbox.docker_manager import (
    get_installed_sandbox_packages,
    get_persistent_auto_approve,
    get_prepare_status,
    get_sandbox_auto_approve,
    get_sandbox_status_info,
    prepare_sandbox_async,
    reset_sandbox,
    set_persistent_auto_approve,
    set_sandbox_auto_approve,
    start_docker_desktop,
)
from nivos.server.routes import _persist_config_section

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])


class AutoApproveRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable auto-approval inside Docker sandbox.")


class PermissionsUpdateRequest(BaseModel):
    allow: Optional[List[str]] = Field(None, description="Allowed command patterns in sandbox (e.g. 'pip install *')")
    deny: Optional[List[str]] = Field(None, description="Denied command patterns across the system")


@router.get("/status")
async def get_sandbox_status(request: Request) -> Dict[str, Any]:
    """Return status of Docker sandbox, container lifecycle, WSL2 check, and setup guidance."""
    return get_sandbox_status_info()


@router.post("/start-daemon")
async def start_daemon(request: Request) -> Dict[str, Any]:
    """Launch Docker Desktop application on the host."""
    success, msg = start_docker_desktop()
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}


@router.post("/prepare")
async def prepare_sandbox_endpoint(request: Request) -> Dict[str, Any]:
    """Trigger background preparation of the sandbox container and package volume."""
    return prepare_sandbox_async()


@router.get("/prepare/status")
async def prepare_status_endpoint(request: Request) -> Dict[str, Any]:
    """Get the current preparation progress and state."""
    return get_prepare_status()


@router.post("/reset")
async def reset_sandbox_endpoint(request: Request) -> Dict[str, Any]:
    """Force remove and reset the Docker sandbox container and package volume."""
    success, msg = reset_sandbox()
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}


@router.post("/auto-approve")
async def update_auto_approve(req: AutoApproveRequest, request: Request) -> Dict[str, Any]:
    """Toggle per-session auto-approval inside Docker sandbox. Never applies to host."""
    res = set_sandbox_auto_approve(req.enabled)
    return {"auto_approve_session": res}


@router.post("/persistent-auto-approve")
async def update_persistent_auto_approve(req: AutoApproveRequest, request: Request) -> Dict[str, Any]:
    """Toggle persistent auto-approval inside Docker sandbox saved in config.toml. Never applies to host."""
    res = set_persistent_auto_approve(req.enabled)
    return {"auto_approve_persistent": res}


@router.get("/permissions")
async def get_permissions(request: Request) -> Dict[str, Any]:
    """Get current [permissions] allow and deny patterns."""
    cfg = load_config()
    perms = getattr(cfg, "permissions", None)
    allow = getattr(perms, "allow", ["pip install *", "npm install *", "git clone *"]) if perms else ["pip install *", "npm install *", "git clone *"]
    deny = getattr(perms, "deny", []) if perms else []
    return {"allow": list(allow), "deny": list(deny)}


@router.post("/permissions")
async def update_permissions(req: PermissionsUpdateRequest, request: Request) -> Dict[str, Any]:
    """Update and persist [permissions] allow/deny patterns into config.toml."""
    updates: Dict[str, Any] = {}
    if req.allow is not None:
        updates["allow"] = [p.strip() for p in req.allow if p.strip()]
    if req.deny is not None:
        updates["deny"] = [p.strip() for p in req.deny if p.strip()]

    if updates:
        try:
            _persist_config_section("permissions", updates)
            load_config.cache_clear()
        except Exception as exc:
            logger.warning("Failed to persist permissions: %s", exc)
            raise HTTPException(status_code=500, detail=f"Failed to persist permissions: {exc}")

    return await get_permissions(request)


@router.get("/packages")
async def get_packages(request: Request) -> Dict[str, Any]:
    """Return list of packages installed inside the Docker sandbox named volume."""
    packages = get_installed_sandbox_packages()
    return {"packages": packages, "count": len(packages)}
