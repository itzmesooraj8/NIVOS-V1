# Nivos Big Build — Roadmap and Phase Tracking

Branch: `feat/big-build`

## Build Rules & Safety
- Central Safety Policy in `src/nivos/security/policy.py` (`is_tool_allowed`)
- Origin enforcement: `LOCAL_CHAT` only for dangerous tools with approval; never for schedules or channels.
- Untrusted content isolation: web, notes, files, Slack messages.
- Protected files: `me.md` is strictly read-only for agents.
- Prompt diet: plain chat = 0 tool schemas; injected memory $\le 300$ tokens; tool request on main tier $\le 700$ tokens.

---

## Phase Status Overview

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | Tool reliability, Claim Guard, neutral clock, central safety policy | done |
| **Phase 2** | Obsidian memory (C:\Nivos\NIVOS-vault, SQLite keyword + bge-m3 background, review screen) | done |
| **Phase 3** | `open_app` tool (Start menu shortcuts, URL schemes, block dangerous binaries) | done |
| **Phase 4** | Docker sandbox for shell tool (Ubuntu container, workspace mount, idle stop) | pending |
| **Phase 5** | Slack direct messages (Socket mode, DM-only, user ID allowlist, read-only tools) | pending |
| **Phase 6** | Voice (Kokoro TTS, CPU speech, settings voice picker, read aloud toggle) | pending |
| **Phase 7** | Nivos logo, icons, desktop shortcut (`nivos install-shortcut`) | pending |
| **Phase 8** | Self-improvement agent (C:\Nivos\self-dev worktree, strict rules, branch `self/<topic>`) | pending |

---

## Phase Details

### Phase 1: Tool Reliability and Central Policy
- [x] Route file/command/install/schedule/search/weather queries (including typos like "Donwload" and phrasings like "make a file", "put this in my downloads") to main tier with tools enabled.
- [x] Tool receipts in chat response (`[Receipt: tool=..., path=..., bytes=..., status=...]`) and logged in Activity.
- [x] Claim Guard in `src/nivos/server/claim_guard.py`: replace unverified action claims with `"No tool ran, so nothing was changed."`.
- [x] Clock: neutral `[now: ...]` without directive text that leaks into replies; broadened direct date/time/day detection with typos.
- [x] Central origin and untrusted-content policy in `src/nivos/security/policy.py` with full unit test coverage.
- [x] Verification: Python tests, frontend tests, frontend build, `uv run nivos doctor`.

### Phase 2: Obsidian Memory
- [x] Vault path saved in `config.toml`, reconnected at start with restart test.
- [x] Read-only indexing of `.md` files (frontmatter, `[[links]]`), skipping `.obsidian` and secrets.
- [x] Fast SQLite keyword search + background `bge-m3` embedding worker by file hash.
- [x] Tools: `search_vault` and `save_to_inbox` (new notes only, path traversal rejection).
- [x] Memory review screen in Settings: proposals for `vault-map.md`, `skill-map.md`, and chat memories.
- [x] `me.md` protection and privacy filter (no vault data to cloud unless permitted).

### Phase 3: `open_app` Tool
- [x] Windows Start menu shortcut scanner & URL schemes (`ms-settings:`, `spotify:`).
- [x] Security blocks for `cmd`, `powershell`, `wscript`, `regedit`, installers, and admin privileges.
- [x] First-launch approval prompt (Once / Always for this app / Deny).
- [x] Activity logging and injection prevention tests.

### Phase 4: Docker Sandbox
- [ ] Containerized shell execution for `workspace/` mount only.
- [ ] Fallback guidance if Docker unavailable.
- [ ] Capabilities dropped, unprivileged, memory capped at 2 GB, idle shutdown.
- [ ] Per-session auto-approve toggle for sandbox (never host).

### Phase 5: Slack Direct Messages
- [ ] Socket Mode integration with local token storage.
- [ ] Minimal DM-only manifest (`im:history`, `im:read`, `im:write`, `chat:write`).
- [ ] Allowlist restricted strictly to configured user ID.
- [ ] Strict read-only tools restriction; Slack text marked untrusted content.

### Phase 6: Voice (Kokoro TTS)
- [ ] `kokoro` TTS backend config and low-priority CPU generation.
- [ ] UI voice selector, Test button, and "Read replies aloud" toggle.
- [ ] Graceful fallback, download progress, and Windows audio handling.

### Phase 7: Nivos Logo, Icons & Shortcut
- [ ] Original dark rounded square with cyan "N" monogram SVG.
- [ ] Favicon, PWA icons (192, 512), apple-touch-icon, and desktop icons.
- [ ] `nivos install-shortcut` command creating user Desktop & Start menu shortcuts.

### Phase 8: Self-Improvement Agent
- [ ] Dedicated git worktree (`C:\Nivos\self-dev`) on `self/<topic>` branches.
- [ ] Protected path invariant enforced in code and pre-commit hook.
- [ ] Review UI in Settings with diff summary and pytest results.
- [ ] Approval command display for manual operator merge.
