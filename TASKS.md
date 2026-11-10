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
| **Phase 2** | Docker made easy: Settings status wizard, named volume package persistence, allow/deny permissions, run_code tool | done |
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
- [x] Sandbox card in Settings: status wizard (installed, running, WSL2 check), "Start Docker Desktop", "Prepare sandbox" with progress bar and step status, "Reset sandbox".
- [x] Persistent "auto-approve inside sandbox" toggle in `config.toml` (`[sandbox] auto_approve = true`) and Settings UI (applies ONLY to sandbox commands from local chat turns; never host/channel/scheduled).
- [x] Named volume for installed packages (`nivos-sandbox-packages`) mounted to `/packages` with `PYTHONUSERBASE=/packages`, size cap (5120 MB), and "installed in sandbox" badge in UI.
- [x] `[permissions]` allow/deny patterns in `config.toml` and Settings editor (defaults: allow `pip install *`, `npm install *`, `git clone *` in sandbox; deny shell download piping `curl | sh` and destructive commands).
- [x] `run_code` tool executing Python and shell scripts inside Docker container with `workspace/` mounted and returning generated artifact receipts.
- [x] Full test coverage in `tests/sandbox/test_sandbox_phase2.py` (9 tests passed).

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
