"""API routes for Self-Improvement Agent status and Review Screen."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from nivos.security.policy import RequestOrigin, is_tool_allowed
from nivos.self_dev.manager import SelfDevManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/self-dev", tags=["self-dev"])


class SelfDevStartReq(BaseModel):
    topic: str = Field(..., description="Topic slug for self-dev branch (self/<topic>)")
    description: str = Field("", description="Goal description")
    files_to_modify: List[str] = Field(default_factory=list, description="Target files")


class SelfDevVerifyReq(BaseModel):
    topic: str
    test_target: str = "tests/"


class SelfDevLessonReq(BaseModel):
    topic: str
    title: str
    content: str


class SelfDevDiscardReq(BaseModel):
    topic: Optional[str] = None


@router.get("/status")
async def get_self_dev_status(request: Request) -> Dict[str, Any]:
    """Report current status of the isolated self-dev worktree."""
    manager = SelfDevManager()
    worktree_exists = manager.worktree_dir.exists()
    branch = ""

    if worktree_exists:
        try:
            import subprocess
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(manager.worktree_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch = res.stdout.strip()
        except Exception:
            branch = ""

    diff_summary = manager.get_diff_summary() if worktree_exists else ""

    return {
        "active": worktree_exists,
        "worktree_path": str(manager.worktree_dir),
        "branch": branch,
        "diff_summary": diff_summary,
        "merge_command": f"git merge {branch}" if branch else "",
    }


@router.get("/reviews")
async def list_self_dev_reviews(request: Request) -> Dict[str, Any]:
    """List completed and pending self-dev session reviews."""
    manager = SelfDevManager()
    sessions = manager.load_review_sessions()
    status = await get_self_dev_status(request)
    return {
        "current": status,
        "history": sessions,
    }


@router.post("/start")
async def start_self_dev(req: SelfDevStartReq, request: Request) -> Dict[str, Any]:
    """Start an isolated self-dev session on branch self/<topic>."""
    # Policy: only LOCAL_CHAT origin and explicit approval
    origin = request.headers.get("x-nivos-origin", RequestOrigin.LOCAL_CHAT.value)
    allowed, reason = is_tool_allowed(
        "self_improve",
        origin=origin,
        user_approved=True,
        params={"topic": req.topic, "action": "start"},
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Self-improvement blocked: {reason}")

    manager = SelfDevManager()
    if req.files_to_modify:
        safe, violations = manager.validate_paths_for_edit(req.files_to_modify)
        if not safe:
            raise HTTPException(
                status_code=400,
                detail=f"Protected path violation: {', '.join(violations)}",
            )

    try:
        worktree_p, branch = manager.ensure_worktree(req.topic)
        return {
            "success": True,
            "branch": branch,
            "worktree_path": str(worktree_p),
            "message": f"Self-improvement worktree ready on '{branch}'.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/verify")
async def verify_self_dev(req: SelfDevVerifyReq, request: Request) -> Dict[str, Any]:
    """Run pytest suite in worktree and record review results."""
    manager = SelfDevManager()
    if not manager.worktree_dir.exists():
        raise HTTPException(status_code=404, detail="No active self-dev worktree to verify.")

    status = await get_self_dev_status(request)
    branch = status.get("branch", f"self/{manager.sanitize_topic(req.topic)}")
    test_res = manager.run_tests(test_target=req.test_target)
    diff = manager.get_diff_summary()

    review_record = {
        "topic": req.topic,
        "branch": branch,
        "worktree": str(manager.worktree_dir),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_results": test_res,
        "diff_summary": diff,
        "merge_command": f"git merge {branch}",
    }
    manager.save_review_session(review_record)

    return {
        "success": test_res["success"],
        "branch": branch,
        "test_results": test_res,
        "diff_summary": diff,
        "merge_command": f"git merge {branch}",
    }


@router.post("/propose-lesson")
async def propose_lesson(req: SelfDevLessonReq, request: Request) -> Dict[str, Any]:
    """Propose an architectural lesson into Nivos-Inbox/."""
    manager = SelfDevManager()
    try:
        path = manager.propose_lesson(req.topic, req.title, req.content)
        return {
            "success": True,
            "file": path.name,
            "full_path": str(path),
            "message": f"Lesson proposal saved to Nivos-Inbox/{path.name}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/discard")
async def discard_self_dev(req: SelfDevDiscardReq, request: Request) -> Dict[str, Any]:
    """Discard worktree changes and prune the worktree."""
    manager = SelfDevManager()
    success = manager.cleanup_worktree(force=True)
    return {"success": success, "message": "Self-improvement worktree discarded and pruned."}
