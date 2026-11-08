"""Workspace routes — manage files and directories in workspace/."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from nivos.core.paths import get_workspace_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspace", tags=["workspace"])


def _validate_safe_path(base_dir: Path, rel_path: str) -> Path:
    """Safely resolve rel_path within base_dir, rejecting traversal attacks."""
    clean = rel_path.replace("\\", "/").strip()
    if clean.startswith("/"):
        clean = clean.lstrip("/")
    if not clean:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    target = (base_dir / clean).resolve()
    try:
        if not target.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="Invalid path: outside workspace directory")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path: outside workspace directory")
    return target


@router.get("/files")
async def list_workspace_files() -> Dict[str, Any]:
    """List all files and directories in the workspace."""
    ws = get_workspace_dir()
    items = []
    for root, dirs, files in os.walk(ws):
        for name in dirs:
            dir_path = Path(root) / name
            rel = dir_path.relative_to(ws).as_posix()
            items.append({
                "path": rel,
                "name": name,
                "is_dir": True,
                "size": 0,
                "modified": dir_path.stat().st_mtime,
            })
        for name in files:
            file_path = Path(root) / name
            rel = file_path.relative_to(ws).as_posix()
            items.append({
                "path": rel,
                "name": name,
                "is_dir": False,
                "size": file_path.stat().st_size,
                "modified": file_path.stat().st_mtime,
            })
    return {"workspace": str(ws), "files": items, "count": len(items)}


@router.post("/upload")
async def upload_workspace_files(
    files: List[UploadFile] = File(...),
    relative_paths: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Upload one or more files or whole directories to the workspace.

    Accepts an optional JSON string of `relative_paths` corresponding to
    the uploaded `files`.
    """
    ws = get_workspace_dir()
    parsed_paths: List[str] = []
    if relative_paths:
        try:
            parsed = json.loads(relative_paths)
            if isinstance(parsed, list):
                parsed_paths = [str(p) for p in parsed]
        except Exception:
            logger.warning("Failed to parse relative_paths JSON, falling back to filenames")

    uploaded = []
    for idx, file in enumerate(files):
        rel_path = parsed_paths[idx] if idx < len(parsed_paths) else file.filename or f"upload_{idx}"
        target_path = _validate_safe_path(ws, rel_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        target_path.write_bytes(content)
        uploaded.append({
            "path": target_path.relative_to(ws).as_posix(),
            "size": len(content),
        })

    logger.info("Uploaded %d files into workspace (%s)", len(uploaded), ws)
    return {
        "success": True,
        "workspace": str(ws),
        "uploaded": uploaded,
        "count": len(uploaded),
    }


@router.delete("/files")
async def delete_workspace_file(path: str) -> Dict[str, Any]:
    """Delete a file or directory from workspace."""
    ws = get_workspace_dir()
    target = _validate_safe_path(ws, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File or directory not found")

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    return {"success": True, "deleted": path}
