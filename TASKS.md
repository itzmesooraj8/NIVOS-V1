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
| **Phase 3** | Apps: Auto-discovery, open any installed app switch, blocklist, always-ask list, winget install_app tool | done |
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
- [x] Installed app auto-discovery (`src/nivos/system/app_discovery.py`): scans Start menu directories (`APPDATA` and `PROGRAMDATA`) and standard Windows utilities, with fuzzy and token matching.
- [x] Switch: "Let nivos open any installed app" (default ON for local chat) added to `config.toml` (`[security] open_any_installed_app = true`) and Settings UI.
- [x] Hardcoded blocklist in code and UI (shells, script hosts, installers, administrative elevation).
- [x] Editable "Always ask first" list in `config.toml` and Settings (default: password managers, banking, remote desktop) requiring confirmation even when master switch is enabled.
- [x] `install_app` tool using winget with exact package IDs (`Publisher.PackageName`), showing Name, Publisher, Version, and Source, asking once per package, never running silently, and never executing raw downloaded binaries.
- [x] Untrusted content fence strictly enforced: turns derived from untrusted content trigger fresh interactive approval even if generally permitted.
- [x] Replaced allow-list section with unified `AppsManagementSection` in Settings UI.
- [x] Full test coverage in `tests/tools/test_apps_phase3.py` (13 tests passed).

### Phase 4: Checkpoints
- [ ] Pre-mutation file snapshots before any agent write/edit/delete (keep 50 newest).
- [ ] "Undo last change" button in UI and `/v1/checkpoints/undo` endpoint.
- [ ] Receipt in chat response (`[Receipt: checkpoint=..., file=..., status=snapshot_taken]`).
