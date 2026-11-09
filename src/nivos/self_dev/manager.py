"""Self-Improvement Agent manager and worktree lifecycle coordinator."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nivos.core.config import DEFAULT_CONFIG_DIR
from nivos.self_dev.policy import is_path_protected

logger = logging.getLogger(__name__)

DEFAULT_WORKTREE_DIR = Path(r"C:\Nivos\self-dev")
DEFAULT_VAULT_INBOX = Path(r"C:\Nivos\NIVOS-vault\Nivos-Inbox")
REVIEWS_FILE = DEFAULT_CONFIG_DIR / "self_dev_reviews.json"


class SelfDevManager:
    """Coordinates the isolated self-improvement git worktree and review workflows."""

    def __init__(
        self,
        repo_dir: Optional[Path] = None,
        worktree_dir: Optional[Path] = None,
        vault_inbox: Optional[Path] = None,
    ) -> None:
        self.repo_dir = (repo_dir or Path(r"C:\Nivos\Nivos-main")).resolve()
        self.worktree_dir = (worktree_dir or DEFAULT_WORKTREE_DIR).resolve()
        self.vault_inbox = (vault_inbox or DEFAULT_VAULT_INBOX).resolve()

    def sanitize_topic(self, topic: str) -> str:
        """Sanitize a topic string into a safe branch slug."""
        clean = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.strip()).strip("-").lower()
        clean = re.sub(r"-+", "-", clean)
        return clean or "improvement"

    def ensure_worktree(self, topic: str) -> Tuple[Path, str]:
        """Ensure an isolated git worktree exists on branch self/<topic>.
        
        Invariants:
        - Worktree is at C:\\Nivos\\self-dev (never in main repo).
        - Branch is strictly self/<topic> (never master, never main).
        - Pre-commit hook is installed enforcing protected paths.
        """
        slug = self.sanitize_topic(topic)
        branch_name = f"self/{slug}"

        if branch_name in ("master", "main", "feat/big-build"):
            raise ValueError(f"Self-improvement cannot use protected branch name: {branch_name}")

        # Check if worktree directory already exists
        if self.worktree_dir.exists():
            # Verify if it is an existing worktree
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "--is-inside-work-tree"],
                    cwd=str(self.worktree_dir),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    # Switch or create branch in existing worktree
                    subprocess.run(
                        ["git", "checkout", "-B", branch_name],
                        cwd=str(self.worktree_dir),
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.install_pre_commit_hook()
                    return self.worktree_dir, branch_name
            except Exception as exc:
                logger.warning("Existing worktree inspection failed: %s; cleaning up.", exc)
                self.cleanup_worktree(force=True)

        # Create fresh worktree
        self.worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "worktree", "add", "-B", branch_name, str(self.worktree_dir), "HEAD"],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Failed to create git worktree at {self.worktree_dir}: {res.stderr}")

        self.install_pre_commit_hook()
        return self.worktree_dir, branch_name

    def install_pre_commit_hook(self) -> None:
        """Install pre-commit hook that enforces the protected paths invariant on every commit."""
        # Locate the git dir for the worktree
        git_loc = self.worktree_dir / ".git"
        git_dir: Optional[Path] = None

        if git_loc.is_file():
            # Worktree .git is a text file: "gitdir: <path>"
            try:
                content = git_loc.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir:"):
                    git_dir = Path(content.split(":", 1)[1].strip())
            except Exception:
                pass
        elif git_loc.is_dir():
            git_dir = git_loc

        if not git_dir or not git_dir.exists():
            # Fall back to repo hooks
            git_dir = self.repo_dir / ".git"

        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-commit"

        hook_script = """#!/bin/sh
# Pre-commit hook: protected paths invariant for self-improvement agent

PROTECTED="safety_rules\\.md|src/nivos/security/|src/nivos/sandbox/|src/nivos/tools/sandboxed_shell\\.py|src/nivos/tools/open_app\\.py|src/nivos/server/claim_guard\\.py|src/nivos/server/vault_routes\\.py|src/nivos/tools/vault_tool\\.py|src/nivos/memory/|me\\.md|nivos-memory/me\\.md|\\.env"

STAGED=$(git diff --cached --name-only)
if [ -z "$STAGED" ]; then
    exit 0
fi

VIOLATIONS=$(echo "$STAGED" | tr '\\\\' '/' | grep -E -i "$PROTECTED")

if [ -n "$VIOLATIONS" ]; then
    echo "" >&2
    echo "[BLOCKED] Self-improvement commit violates protected path invariant:" >&2
    echo "$VIOLATIONS" | while IFS= read -r line; do
        [ -n "$line" ] && echo "  - $line" >&2
    done
    echo "" >&2
    echo "Modification of security, sandbox, approval, and me.md paths is strictly forbidden." >&2
    echo "" >&2
    exit 1
fi

exit 0
"""
        hook_path.write_text(hook_script, encoding="utf-8")
        try:
            # Attempt to make executable if supported
            os.chmod(hook_path, 0o755)
        except Exception:
            pass

    def validate_paths_for_edit(self, file_paths: List[str]) -> Tuple[bool, List[str]]:
        """Validate that none of the target paths violate the protected path invariant."""
        violations = []
        for p in file_paths:
            is_prot, reason = is_path_protected(p)
            if is_prot:
                violations.append(f"{p}: {reason}")
        return len(violations) == 0, violations

    def get_diff_summary(self, worktree_p: Optional[Path] = None) -> str:
        """Return the git diff summary for current changes in the worktree."""
        target = worktree_p or self.worktree_dir
        if not target.exists():
            return "No active worktree"

        try:
            res = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=10,
            )
            status_text = res.stdout.strip()

            diff_res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=15,
            )
            diff_text = diff_res.stdout.strip()
            
            if not diff_text:
                # Check diff against parent commit if HEAD was committed
                parent_diff = subprocess.run(
                    ["git", "diff", "HEAD~1"],
                    cwd=str(target),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                diff_text = parent_diff.stdout.strip()

            return f"Status:\n{status_text}\n\nDiff:\n{diff_text[:4000]}"
        except Exception as exc:
            return f"Error gathering diff: {exc}"

    def run_tests(self, worktree_p: Optional[Path] = None, test_target: str = "tests/") -> Dict[str, Any]:
        """Run pytest in the isolated worktree directory."""
        target = worktree_p or self.worktree_dir
        if not target.exists():
            return {"success": False, "summary": "Worktree does not exist", "passed": 0, "failed": 1}

        try:
            res = subprocess.run(
                ["uv", "run", "pytest", test_target, "-q"],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = (res.stdout + "\n" + res.stderr).strip()
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "summary": output[-1000:],
            }
        except Exception as exc:
            return {"success": False, "summary": f"Test runner execution error: {exc}", "exit_code": -1}

    def propose_lesson(self, topic: str, lesson_title: str, content: str) -> Path:
        """Write a lesson proposal into Nivos-Inbox/ following strict new-notes-only invariant."""
        slug = self.sanitize_topic(topic)
        self.vault_inbox.mkdir(parents=True, exist_ok=True)

        target_file = self.vault_inbox / f"lesson-{slug}.md"
        counter = 1
        while target_file.exists():
            target_file = self.vault_inbox / f"lesson-{slug}-{counter}.md"
            counter += 1

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        formatted = f"""---
title: {lesson_title or f"Lesson: {topic.title()}"}
type: lesson
origin: self_dev
topic: {topic}
date: {now_str}
status: pending_review
---

# {lesson_title or f"Lesson: {topic.title()}"}

{content.strip()}
"""
        target_file.write_text(formatted, encoding="utf-8")
        return target_file

    def cleanup_worktree(self, force: bool = False) -> bool:
        """Safely prune and remove the isolated self-dev worktree."""
        try:
            if self.worktree_dir.exists():
                subprocess.run(
                    ["git", "worktree", "remove", str(self.worktree_dir), "--force"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if self.worktree_dir.exists():
                    shutil.rmtree(self.worktree_dir, ignore_errors=True)
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return True
        except Exception as exc:
            logger.warning("Cleanup worktree failed: %s", exc)
            return False

    def save_review_session(self, session_data: Dict[str, Any]) -> None:
        """Persist self-dev session review record for the review UI."""
        REVIEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
        sessions = self.load_review_sessions()
        sessions.append(session_data)
        REVIEWS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")

    def load_review_sessions(self) -> List[Dict[str, Any]]:
        """Load persisted self-dev session reviews."""
        if not REVIEWS_FILE.exists():
            return []
        try:
            return json.loads(REVIEWS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
