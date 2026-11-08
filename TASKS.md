# Development Tasks & Roadmap

Branch: `feat/workspace-shell-mcp`

## Safety & Governance
- [x] Create `SAFETY_RULES.md` (Branch isolation, test verification, command approval, license protection)
- [x] Create `TASKS.md` tracking development progress

## Feature 1: Workspace Upload Endpoint & Chat UI "+" Button
- [x] Backend: Create `src/nivos/server/workspace_routes.py`
  - `POST /v1/workspace/upload`: Multipart file upload preserving relative paths under `workspace/` with path traversal protection
  - `GET /v1/workspace/files`: List files in `workspace/`
  - Mount router in `src/nivos/server/app.py`
- [x] Frontend:
  - Add API client methods in `frontend/src/lib/api.ts` (`uploadToWorkspace`, `fetchWorkspaceFiles`)
  - Update `frontend/src/components/Chat/InputArea.tsx`: Add `+` button with file and folder upload options (`webkitdirectory`)
  - Add toast feedback and agent awareness of uploaded workspace files
- [x] Tests & Verification:
  - Frontend build: `npm --prefix frontend run build` (passed)
  - Unit tests for upload & file list: `tests/server/test_workspace_routes.py` (4 passed)
- [x] Summary report after Step 1

## Feature 2: Sandboxed Shell Tool Limited to `workspace/` with Approval Prompt
- [x] Backend: Create `src/nivos/tools/sandboxed_shell.py`
  - Restrict allowed commands strictly to: `git clone`, `pip install`, `npm install`
  - Restrict working directory strictly to `workspace/` (create on demand, prevent `..` path traversal)
  - Interactive approve/deny prompt: Integration with `ApprovalStore` / callback confirmation before execution
  - Register tool in `ToolRegistry` as `sandboxed_shell`
- [x] Tests & Verification:
  - Unit tests in `tests/tools/test_sandboxed_shell.py` verifying whitelisted commands, path restrictions, and approval requirements (9 passed)
- [x] Summary report after Step 2

## Feature 3: MCP Client Integration, Settings Page & Simple Plugin Loader
- [x] Backend MCP Client & Tools:
  - Expose MCP server tools to agents like native skills
  - Add MCP server management endpoints in `src/nivos/server/mcp_routes.py`:
    - `GET /v1/mcp/servers`: List configured servers and their discovered tools
    - `POST /v1/mcp/servers`: Add/update MCP server
    - `DELETE /v1/mcp/servers/{name}`: Remove MCP server
- [x] Plugin Loader:
  - Create `src/nivos/plugins/loader.py` to scan and load plugins from `plugins/` and `~/.nivos/plugins/`
  - Plugin discovery manifest and dynamic tool registration
  - Plugin endpoints: `GET /v1/plugins`, `POST /v1/plugins/reload`
- [x] Frontend Settings:
  - Add "MCP Servers & Plugins" section in `frontend/src/pages/SettingsPage.tsx`
  - Forms to view, add, and remove MCP servers and list active plugins
- [x] Tests & Verification:
  - Frontend build: `npm --prefix frontend run build` (passed)
  - Unit tests: `tests/plugins/test_plugin_loader.py` & `tests/server/test_mcp_routes.py` (5 passed)
  - Frontend Vitest suite: 20/20 files, 102/102 passed
  - System health check: `uv run nivos doctor` (15 passed, 0 failures)
- [x] Summary report after Step 3
