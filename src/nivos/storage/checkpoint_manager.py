"""Checkpoint manager — pre-mutation file snapshotting and undo support.

Safety Invariants:
1. Before any agent write/edit/delete of files, take a snapshot.
2. Maintain max 50 newest snapshots, auto-pruning older ones.
3. Support "Undo last change" by restoring previous file contents or deleting newly created files.
4. Issue formatted receipts: [Receipt: checkpoint=cp_..., file=..., status=snapshot_taken]
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.events import EventType, get_event_bus
from nivos.core.paths import get_config_dir, get_workspace_dir

logger = logging.getLogger(__name__)

_MAX_CHECKPOINTS = 50
_lock = threading.Lock()


@dataclass
class CheckpointEntry:
    id: str
    timestamp: float
    file_path: str
    display_path: str
    operation: str  # "write", "append", "patch", "inbox", "delete"
    existed_before: bool
    backup_file: Optional[str]
    size_bytes: int


def get_checkpoint_dir() -> Path:
    """Return the directory where file snapshots and manifest are persisted."""
    cp_dir = get_config_dir() / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)
    return cp_dir


def _get_manifest_path() -> Path:
    return get_checkpoint_dir() / "manifest.json"


def _load_manifest() -> List[Dict[str, Any]]:
    path = _get_manifest_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        logger.debug("Failed loading checkpoint manifest: %s", exc)
        return []


def _save_manifest(entries: List[Dict[str, Any]]) -> None:
    path = _get_manifest_path()
    try:
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed saving checkpoint manifest: %s", exc)


def _prune_old_checkpoints(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure at most 50 checkpoints are kept, deleting older backup files."""
    if len(entries) <= _MAX_CHECKPOINTS:
        return entries

    # Entries are ordered oldest to newest; keep last 50
    prune_count = len(entries) - _MAX_CHECKPOINTS
    to_delete = entries[:prune_count]
    kept = entries[prune_count:]

    cp_dir = get_checkpoint_dir()
    for item in to_delete:
        bk = item.get("backup_file")
        if bk:
            bk_path = cp_dir / bk
            if bk_path.exists():
                try:
                    bk_path.unlink()
                except Exception:
                    pass

    return kept


def create_checkpoint(
    target_path: Path | str,
    operation: str = "write",
) -> Dict[str, Any]:
    """Take a pre-mutation snapshot of target_path before modifying or creating it.

    Returns the checkpoint dictionary.
    """
    with _lock:
        resolved = Path(target_path).expanduser().resolve()
        cp_id = f"cp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()

        # Display-friendly relative path if inside workspace
        ws_dir = get_workspace_dir().resolve()
        try:
            if resolved == ws_dir or resolved.is_relative_to(ws_dir):
                display_p = str(resolved.relative_to(ws_dir)).replace("\\", "/")
            else:
                display_p = resolved.name
        except Exception:
            display_p = resolved.name

        cp_dir = get_checkpoint_dir()
        existed = resolved.exists()
        backup_file_name: Optional[str] = None
        size_bytes = 0

        if existed and resolved.is_file():
            backup_file_name = f"{cp_id}.bak"
            backup_path = cp_dir / backup_file_name
            try:
                shutil.copy2(resolved, backup_path)
                size_bytes = resolved.stat().st_size
            except Exception as exc:
                logger.warning("Failed to create snapshot backup for '%s': %s", resolved, exc)

        entry = {
            "id": cp_id,
            "timestamp": now,
            "file_path": str(resolved),
            "display_path": display_p,
            "operation": operation,
            "existed_before": existed,
            "backup_file": backup_file_name,
            "size_bytes": size_bytes,
        }

        manifest = _load_manifest()
        manifest.append(entry)
        manifest = _prune_old_checkpoints(manifest)
        _save_manifest(manifest)

        # Log checkpoint creation
        receipt = f"[Receipt: checkpoint={cp_id}, file={display_p}, status=snapshot_taken]"
        try:
            bus = get_event_bus()
            bus.publish(
                EventType.TOOL_EXECUTE,
                {
                    "tool": "checkpoint",
                    "checkpoint_id": cp_id,
                    "file": display_p,
                    "operation": operation,
                    "status": "snapshot_taken",
                    "receipt": receipt,
                },
            )
        except Exception:
            pass

        entry["receipt"] = receipt
        return entry


def get_latest_checkpoint() -> Optional[Dict[str, Any]]:
    """Return the most recent checkpoint entry, or None if none exist."""
    with _lock:
        manifest = _load_manifest()
        if not manifest:
            return None
        return manifest[-1]


def list_checkpoints(limit: int = 50) -> List[Dict[str, Any]]:
    """Return up to limit recent checkpoints (newest first)."""
    with _lock:
        manifest = _load_manifest()
        return list(reversed(manifest[-limit:]))


def undo_checkpoint(checkpoint_id: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Revert the file change recorded in the specified checkpoint (or latest if omitted).

    Returns:
        (success: bool, message: str, checkpoint_entry: Optional[Dict])
    """
    with _lock:
        manifest = _load_manifest()
        if not manifest:
            return False, "No checkpoints available to undo.", None

        target_idx = -1
        if checkpoint_id:
            for i, cp in enumerate(manifest):
                if cp["id"] == checkpoint_id:
                    target_idx = i
                    break
            if target_idx == -1:
                return False, f"Checkpoint '{checkpoint_id}' not found.", None
        else:
            target_idx = len(manifest) - 1

        cp = manifest.pop(target_idx)
        _save_manifest(manifest)

        file_p = Path(cp["file_path"])
        cp_dir = get_checkpoint_dir()
        display_p = cp.get("display_path", file_p.name)

        try:
            if cp["existed_before"]:
                bk_name = cp.get("backup_file")
                if not bk_name:
                    return False, f"Backup file missing for checkpoint {cp['id']}.", cp
                bk_path = cp_dir / bk_name
                if not bk_path.exists():
                    return False, f"Snapshot backup file {bk_name} not found on disk.", cp

                file_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bk_path, file_p)
                bk_path.unlink(missing_ok=True)
                msg = f"Successfully restored '{display_p}' to pre-mutation state."
            else:
                # File was newly created by the agent; undo means deleting it
                if file_p.exists() and file_p.is_file():
                    file_p.unlink()
                    msg = f"Successfully removed newly created file '{display_p}'."
                else:
                    msg = f"File '{display_p}' was already removed."

            receipt = f"[Receipt: checkpoint={cp['id']}, file={display_p}, status=undone]"
            try:
                bus = get_event_bus()
                bus.publish(
                    EventType.TOOL_EXECUTE,
                    {
                        "tool": "checkpoint_undo",
                        "checkpoint_id": cp["id"],
                        "file": display_p,
                        "status": "undone",
                        "receipt": receipt,
                    },
                )
            except Exception:
                pass

            cp["receipt"] = receipt
            return True, msg, cp
        except Exception as exc:
            logger.warning("Failed undoing checkpoint %s: %s", cp["id"], exc)
            return False, f"Failed undoing change: {exc}", cp

