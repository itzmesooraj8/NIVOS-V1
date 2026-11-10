# Nivos Build 1: Trust and Install — Roadmap and Phase Tracking

Branch: `feat/trust-and-install`

## Standing Invariants
- One commit per phase. Run Python tests, frontend tests/build, and `uv run nivos doctor` after each phase.
- Do not merge, do not tag, never touch `master`.
- `me.md` and `Nivos-Memory/me.md` are strictly read-only for agents.
- Prompt diet: plain chat = 0 tool schemas; injected memory $\le 300$ tokens; tool requests on main tier $\le 700$ tokens.
- Central Safety Policy in `src/nivos/security/policy.py` (`is_tool_allowed`).

---

## Phase Status Overview

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | Messaging: Slack allowlist, Telegram private DM, remove SendBlue/iMessage, read-only tools, no approvals from channels, sanitized notifications | done |
| **Phase 2** | Docker made easy: Settings status wizard, named volume package persistence, allow/deny permissions, run_code tool | pending |
| **Phase 3** | Apps: Auto-discovery, open any installed app switch, blocklist, always-ask list, winget install_app tool | pending |
| **Phase 4** | Checkpoints: Pre-mutation snapshots (keep 50), Undo last change button & chat receipt | pending |

---

## Phase Details

### Phase 1: Messaging (Slack, Telegram, Channel Restrictions)
- [x] Removed duplicate "Slack Direct Messages" section from `SettingsPage.tsx`.
- [x] Maintained Slack in Messaging Channels UI (`DataSourcesPage.tsx`, `AgentsPage.tsx`) with minimal DM-only manifest (`chat:write`, `im:*`) and "My Slack User ID" allow-list field.
- [x] Added Telegram in Messaging Channels UI with @BotFather guided instructions, masked bot token, and "My Telegram User ID" allow-list field.
- [x] Removed iMessage and SMS (SendBlue) from frontend UI, connectors catalog, `ChannelRegistry`, and `ConnectorRegistry`.
- [x] Enforced in code: channel requests are strictly read-only (`web_search`, `get_weather`, `search_vault`, `calculator`, `time`).
- [x] Telegram channel hardened: `chat.type == "private"` only (strictly ignores groups/channels), matches sender against allow-list.
- [x] Approvals strictly forbidden from messaging channels in code (`TelegramChannel`, `SlackChannel`, `ChannelBridge`, and `/v1/approvals/{id}/approve` endpoint).
- [x] Channel notifications wired for task completion and approval requirements with strict secret/file-content sanitization (`sanitize_notification_text`).
- [x] Full test coverage in `tests/channels/test_channels_policy.py` and `tests/server/test_approval_routes.py`.

### Phase 2: Docker Made Easy
- [ ] Sandbox card in Settings: status wizard (installed, running, WSL2 check), "Start Docker Desktop", "Prepare sandbox" with progress, "Reset sandbox".
- [ ] Persistent "auto-approve inside sandbox" toggle in config and Settings (applies ONLY to sandbox commands from local chat turns; never host/channel/scheduled).
- [ ] Named volume for installed packages (`nivos-sandbox-packages`) with size cap and "installed in sandbox" badge in UI.
- [ ] `[permissions]` allow/deny patterns in `config.toml` (defaults: allow `pip install *`, `npm install *`, `git clone *` in sandbox; deny shell download piping `curl | sh` and destructive commands).
- [ ] `run_code` tool executing python in Docker with `workspace/` mounted.

### Phase 3: Apps
- [ ] Installed app auto-discovery (Start menu + winget list), matching by name ("open spotify").
- [ ] Switch: "Let nivos open any installed app" (default on for local chat).
- [ ] Blocklist (shells, script hosts, installers, admin); editable "always ask first" list (password managers, banking, remote desktop).
- [ ] `install_app` tool using winget with exact package IDs only (name, publisher, version, source; ask once per package, never silent, never run downloaded raw installers).
- [ ] Untrusted content fence triggers fresh approval for dangerous actions.

### Phase 4: Checkpoints
- [ ] Pre-mutation file snapshots before any agent write/edit/delete (keep 50 newest).
- [ ] "Undo last change" button in UI and `/v1/checkpoints/undo` endpoint.
- [ ] Receipt in chat response (`[Receipt: checkpoint=..., file=..., status=snapshot_taken]`).

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
- [x] Containerized shell execution for `workspace/` mount only.
- [x] Fallback guidance if Docker unavailable.
- [x] Capabilities dropped, unprivileged, memory capped at 2 GB, idle shutdown.
- [x] Per-session auto-approve toggle for sandbox (never host).

### Phase 5: Slack Direct Messages
- [x] Socket Mode integration with local token storage.
- [x] Minimal DM-only manifest (`im:history`, `im:read`, `im:write`, `chat:write`).
- [x] Allowlist restricted strictly to configured user ID.
- [x] Strict read-only tools restriction; Slack text marked untrusted content.

### Phase 6: Voice (Kokoro TTS)
- [x] `kokoro` TTS backend config and low-priority CPU generation.
- [x] UI voice selector, Test button, and "Read replies aloud" toggle.
- [x] Graceful fallback, download progress, and Windows audio handling.

### Phase 7: Nivos Logo, Icons & Shortcut
- [x] Original dark rounded square with cyan "N" monogram SVG.
- [x] Favicon, PWA icons (192, 512), apple-touch-icon, and desktop icons.
- [x] `nivos install-shortcut` command creating user Desktop & Start menu shortcuts.

### Phase 8: Self-Improvement Agent
- [x] Dedicated git worktree (`C:\Nivos\self-dev`) on `self/<topic>` branches.
- [x] Protected path invariant enforced in code and git pre-commit hook (SAFETY_RULES.md, security, sandbox, shell, open_app, claim_guard, vault_routes, memory, me.md, .env).
- [x] Review UI in Settings with diff summary, Test Runner button, Discard button, and manual merge instructions.
- [x] Lesson proposals saved strictly to `Nivos-Inbox/` with new-notes-only invariant and frontmatter.
- [x] Central policy gate: local chat origin only, requires operator approval, blocked on Slack / schedules / untrusted input.
