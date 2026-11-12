import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import {
  Palette,
  Globe,
  Cpu,
  Database,
  Info,
  Check,
  Sun,
  Moon,
  Monitor,
  Download,
  Upload,
  Trash2,
  Mic,
  Key,
  Search,
  Brain,
  RefreshCw,
  Server,
  Package,
  Plus,
  X,
  Terminal,
  Volume2,
  Square,
  Copy,
} from 'lucide-react';
import { useAppStore, type ThemeMode } from '../lib/store';
import {
  checkHealth,
  fetchSpeechHealth,
  fetchTtsHealth,
  type TtsHealth,
  fetchTtsConfig,
  updateTtsConfig,
  synthesizeSpeech,
  getMemoryStats,
  getInferenceSource,
  setInferenceSource,
  getCloudKeyStatus,
  saveCloudKey,
  getPrivacySettings,
  savePrivacySettings,
  fetchToolCredentialStatus,
  saveToolCredentials,
  deleteToolCredential,
  isTauri,
  fetchOllamaModels,
  fetchModels,
  fetchServerInfo,
  fetchDiagnostics,
  fetchMcpServers,
  addMcpServer,
  deleteMcpServer,
  fetchPlugins,
  reloadPlugins,
  getRoutingSettings,
  saveRoutingSettings,
  getContextSettings,
  saveContextSettings,
  fetchVaultConfig,
  saveVaultConfig,
  fetchVaultProposals,
  approveVaultProposal,
  rejectVaultProposal,
  syncVault,
  fetchAllowedApps,
  updateAllowedApp,
  fetchAppSettings,
  updateAppSettings,
  fetchDiscoveredApps,
  type AppSettings,
  type DiscoveredApp,
  fetchSandboxStatus,
  resetSandbox,
  updateSandboxAutoApprove,
  updateSandboxPersistentAutoApprove,
  startDockerDesktop,
  prepareSandbox,
  fetchPrepareStatus,
  fetchSandboxPermissions,
  updateSandboxPermissions,
  type SandboxStatus,
  type InferenceSource,
  type MemoryStats,
  type McpServerConfig,
  type PluginInfo,
  type VaultConfig,
  type VaultProposal,
} from '../lib/api';
import type { RoutingSettings, ContextSettings } from '../types';
import { isAutoUpdateDisabled, setAutoUpdateDisabled } from '../components/Desktop/UpdateChecker';
import { SelfDevReviewSection } from '../components/SelfDevReviewSection';

const CLOUD_KEY_STATUS_CHANGED = 'nivos-cloud-key-status-changed';

export function DiagnosticsButton() {
  const [status, setStatus] = useState<'idle' | 'loading' | 'copied' | 'error'>('idle');

  const handleCopyDiagnostics = async () => {
    setStatus('loading');
    try {
      const data = await fetchDiagnostics();
      const text = data.report || '';
      let copied = false;
      if (navigator?.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(text);
          copied = true;
        } catch {
          copied = false;
        }
      }
      if (!copied && typeof document !== 'undefined') {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
          copied = document.execCommand('copy');
        } finally {
          document.body.removeChild(ta);
        }
      }
      if (copied) {
        setStatus('copied');
        toast.success('Diagnostics copied (no secrets included)');
        setTimeout(() => setStatus('idle'), 2500);
      } else {
        setStatus('error');
        toast.error('Unable to access clipboard');
        setTimeout(() => setStatus('idle'), 2500);
      }
    } catch {
      setStatus('error');
      toast.error('Failed to collect diagnostics');
      setTimeout(() => setStatus('idle'), 2500);
    }
  };

  return (
    <button
      type="button"
      onClick={() => {
        void handleCopyDiagnostics();
      }}
      disabled={status === 'loading'}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
      style={{
        background: status === 'copied' ? 'var(--color-accent-subtle)' : 'var(--color-bg-tertiary)',
        border: '1px solid var(--color-border)',
        color: status === 'copied' ? 'var(--color-success)' : 'var(--color-text)',
      }}
    >
      {status === 'copied' ? <Check size={12} /> : <Copy size={12} />}
      {status === 'loading'
        ? 'Collecting...'
        : status === 'copied'
          ? 'Copied'
          : status === 'error'
            ? 'Failed'
            : 'Diagnostics'}
    </button>
  );
}

export function OllamaModelList() {
  const [models, setModels] = useState<Array<{ name: string; size: number }>>([]);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const next = await fetchOllamaModels();
        if (!active) return;
        setModels(next);
        if (next.length > 0) {
          fetchModels()
            .then((all) => {
              if (active && all.length > 0) useAppStore.getState().setModels(all);
            })
            .catch(() => {});
          fetchServerInfo()
            .then((info) => {
              if (active) useAppStore.getState().setServerInfo(info);
            })
            .catch(() => {});
        }
      } catch {
        if (active) setModels([]);
      }
    };
    void refresh();
    const interval = setInterval(() => {
      void refresh();
    }, 8_000);
    const onStatus = (ev: Event) => {
      const detail = (ev as CustomEvent<{ reachable?: boolean }>).detail;
      if (detail?.reachable) void refresh();
    };
    const onFocus = () => {
      void refresh();
    };
    window.addEventListener('nivos:ollama-status', onStatus);
    window.addEventListener('focus', onFocus);
    return () => {
      active = false;
      clearInterval(interval);
      window.removeEventListener('nivos:ollama-status', onStatus);
      window.removeEventListener('focus', onFocus);
    };
  }, []);
  if (models.length === 0) return <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>No models loaded</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {models.map(m => (
        <span key={m.name} className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]"
          style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-success)', display: 'inline-block' }} />
          {m.name} ({(m.size / 1e9).toFixed(1)} GB)
        </span>
      ))}
    </div>
  );
}

function ApiKeyInput({
  keyName,
  placeholder,
  toolName,
}: {
  keyName: string;
  placeholder: string;
  toolName?: string;
}) {
  const [value, setValue] = useState('');
  const [saved, setSaved] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const status = toolName
        ? await fetchToolCredentialStatus(toolName)
        : await getCloudKeyStatus();
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [keyName, toolName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  const save = async (v: string) => {
    const next = v.trim();
    if (!next) return;
    setError('');
    try {
      if (toolName) {
        await saveToolCredentials(toolName, { [keyName]: next });
      } else {
        await saveCloudKey(keyName, next);
      }
      setValue('');
      setHasKey(true);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to save API key');
    }
  };

  const remove = async () => {
    setError('');
    try {
      if (toolName) {
        await deleteToolCredential(toolName, keyName);
      } else {
        await saveCloudKey(keyName, '');
      }
      setValue('');
      setHasKey(false);
      setSaved(true);
      window.dispatchEvent(new Event(CLOUD_KEY_STATUS_CHANGED));
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Failed to remove API key');
    }
  };

  return (
    <div className="flex items-center gap-2">
      <input
        type="password"
        value={value}
        onChange={e => setValue(e.target.value)}
        onBlur={() => { if (value.trim()) void save(value); }}
        placeholder={hasKey ? 'Saved locally' : placeholder}
        className="w-48 px-2 py-1 rounded text-xs"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
      {hasKey && (
        <button
          onClick={() => void remove()}
          className="px-2 py-1 rounded text-[10px] cursor-pointer"
          style={{ color: 'var(--color-error)', border: '1px solid var(--color-error)' }}
        >
          Remove
        </button>
      )}
      {saved && <span className="text-[10px]" style={{ color: 'var(--color-success)' }}>Saved</span>}
      {error && <span className="text-[10px]" style={{ color: 'var(--color-error)' }}>{error}</span>}
    </div>
  );
}

function CloudProviderStatus({ label, keyName }: { label: string; keyName: string }) {
  const [hasKey, setHasKey] = useState(false);
  const desktopKeyStorage = isTauri();

  const refresh = useCallback(async () => {
    if (!desktopKeyStorage) {
      setHasKey(false);
      return;
    }
    try {
      const status = await getCloudKeyStatus();
      setHasKey(!!status[keyName]);
    } catch {
      setHasKey(false);
    }
  }, [desktopKeyStorage, keyName]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
    return () => window.removeEventListener(CLOUD_KEY_STATUS_CHANGED, refresh);
  }, [refresh]);

  return (
    <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
        background: hasKey ? 'var(--color-success)' : 'var(--color-text-tertiary)',
      }} />
      {label}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-xl p-5"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
    >
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--color-text)' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function SettingRow({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
      <div className="min-w-0">
        <div className="text-sm" style={{ color: 'var(--color-text)' }}>{label}</div>
        {description && (
          <div className="text-xs mt-0.5 break-words" style={{ color: 'var(--color-text-tertiary)' }}>{description}</div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

type MemoryStatus =
  | { kind: 'loading' }
  | { kind: 'ready'; stats: MemoryStats }
  | { kind: 'error'; message: string };

export function MemoryStatusRow({
  status,
  onRetry,
}: {
  status: MemoryStatus;
  onRetry: () => void;
}) {
  const stats = status.kind === 'ready' ? status.stats : null;
  const description = status.kind === 'error'
    ? status.message
    : status.kind === 'loading'
      ? 'Checking memory backend...'
      : stats?.backend === 'none'
        ? 'Memory is not configured'
        : `${stats?.backend} backend — ${stats?.entries} entries`;
  const label = status.kind === 'loading'
    ? 'Checking...'
    : stats?.backend === 'none'
      ? 'Not configured'
      : stats
        ? `${stats.entries} entries`
        : 'Unavailable';

  return (
    <SettingRow label="Memory status" description={description}>
      <div className="flex items-center gap-2">
        <Brain size={14} style={{ color: stats && stats.backend !== 'none' ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }} />
        <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-text-secondary)' }}>
          {label}
        </span>
        {status.kind === 'error' && (
          <button
            type="button"
            onClick={onRetry}
            className="text-xs underline cursor-pointer whitespace-nowrap"
            style={{ color: 'var(--color-accent)' }}
          >
            Retry
          </button>
        )}
      </div>
    </SettingRow>
  );
}

function McpServersSection() {
  const [servers, setServers] = useState<McpServerConfig[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [serverName, setServerName] = useState('');
  const [serverType, setServerType] = useState<'command' | 'url'>('command');
  const [commandStr, setCommandStr] = useState('');
  const [urlStr, setUrlStr] = useState('');
  const [argsStr, setArgsStr] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  const loadData = useCallback(async () => {
    try {
      const [srvRes, plugRes] = await Promise.all([
        fetchMcpServers().catch(() => ({ servers: [], count: 0 })),
        fetchPlugins().catch(() => ({ plugins: [], count: 0 })),
      ]);
      setServers(srvRes.servers || []);
      setPlugins(plugRes.plugins || []);
    } catch {}
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleAddServer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!serverName.trim()) {
      setErrorMsg('Server name is required');
      return;
    }
    setErrorMsg('');
    setSubmitting(true);
    try {
      const args = argsStr.trim() ? argsStr.trim().split(/\s+/) : [];
      const res = await addMcpServer({
        name: serverName.trim(),
        command: serverType === 'command' ? commandStr.trim() : undefined,
        url: serverType === 'url' ? urlStr.trim() : undefined,
        args: serverType === 'command' && args.length > 0 ? args : undefined,
        enabled: true,
      });
      if (res.error) {
        toast.error(`Added server, but connection note: ${res.error}`);
      } else {
        toast.success(`Connected MCP server '${serverName.trim()}' (${res.count} tools discovered)`);
      }
      setShowAddForm(false);
      setServerName('');
      setCommandStr('');
      setUrlStr('');
      setArgsStr('');
      await loadData();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to add MCP server');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteServer = async (name: string) => {
    try {
      await deleteMcpServer(name);
      toast.success(`Removed MCP server '${name}'`);
      setServers((prev) => prev.filter((s) => s.name !== name));
    } catch (err: any) {
      toast.error(err.message || 'Failed to remove MCP server');
    }
  };

  const handleReloadPlugins = async () => {
    setReloading(true);
    try {
      const res = await reloadPlugins();
      setPlugins(res.plugins || []);
      toast.success(`Loaded ${res.count} plugin${res.count === 1 ? '' : 's'}`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to reload plugins');
    } finally {
      setReloading(false);
    }
  };

  return (
    <Section title="MCP Servers & Plugins">
      {/* Configured MCP Servers */}
      <SettingRow
        label="MCP Servers"
        description="Connect Model Context Protocol servers to grant tools directly to agents"
      >
        <button
          type="button"
          onClick={() => setShowAddForm(!showAddForm)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors"
          style={{
            background: showAddForm ? 'var(--color-bg-tertiary)' : 'var(--color-accent)',
            color: showAddForm ? 'var(--color-text)' : 'white',
          }}
        >
          {showAddForm ? <X size={14} /> : <Plus size={14} />}
          {showAddForm ? 'Cancel' : 'Add Server'}
        </button>
      </SettingRow>

      {/* Add Server Inline Form */}
      {showAddForm && (
        <form
          onSubmit={handleAddServer}
          className="p-4 rounded-xl flex flex-col gap-3 my-2"
          style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
        >
          <div className="text-xs font-semibold" style={{ color: 'var(--color-text)' }}>
            Add New MCP Server
          </div>
          {errorMsg && (
            <div className="text-xs text-red-400 p-2 rounded" style={{ background: 'rgba(239, 68, 68, 0.1)' }}>
              {errorMsg}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>Server Name</label>
              <input
                type="text"
                placeholder="e.g. filesystem, github, weather"
                value={serverName}
                onChange={(e) => setServerName(e.target.value)}
                required
                className="w-full text-xs px-3 py-2 rounded-lg outline-none"
                style={{ background: 'var(--color-surface)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              />
            </div>
            <div>
              <label className="block text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>Transport Type</label>
              <select
                value={serverType}
                onChange={(e) => setServerType(e.target.value as any)}
                className="w-full text-xs px-3 py-2 rounded-lg outline-none"
                style={{ background: 'var(--color-surface)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="command">Stdio (Local Command / npx)</option>
                <option value="url">HTTP / SSE Endpoint</option>
              </select>
            </div>
          </div>

          {serverType === 'command' ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>Command</label>
                <input
                  type="text"
                  placeholder="e.g. npx, uvx, python"
                  value={commandStr}
                  onChange={(e) => setCommandStr(e.target.value)}
                  required
                  className="w-full text-xs px-3 py-2 rounded-lg outline-none font-mono"
                  style={{ background: 'var(--color-surface)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                />
              </div>
              <div>
                <label className="block text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>Arguments (space separated)</label>
                <input
                  type="text"
                  placeholder="e.g. -y @modelcontextprotocol/server-filesystem ./workspace"
                  value={argsStr}
                  onChange={(e) => setArgsStr(e.target.value)}
                  className="w-full text-xs px-3 py-2 rounded-lg outline-none font-mono"
                  style={{ background: 'var(--color-surface)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                />
              </div>
            </div>
          ) : (
            <div>
              <label className="block text-[11px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>Server URL</label>
              <input
                type="url"
                placeholder="https://... or http://localhost:8000/sse"
                value={urlStr}
                onChange={(e) => setUrlStr(e.target.value)}
                required
                className="w-full text-xs px-3 py-2 rounded-lg outline-none font-mono"
                style={{ background: 'var(--color-surface)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              />
            </div>
          )}

          <div className="flex justify-end gap-2 mt-1">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-3 py-1.5 rounded-lg text-xs cursor-pointer"
              style={{ background: 'transparent', color: 'var(--color-text-secondary)' }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-opacity disabled:opacity-50"
              style={{ background: 'var(--color-accent)', color: 'white' }}
            >
              {submitting ? 'Connecting...' : 'Connect & Save'}
            </button>
          </div>
        </form>
      )}

      {/* Server Cards List */}
      <div className="flex flex-col gap-2 my-2">
        {servers.length === 0 ? (
          <div className="text-xs py-2 px-3 rounded-lg" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-tertiary)' }}>
            No MCP servers configured yet. Click "Add Server" to connect filesystem, database, or external tool servers.
          </div>
        ) : (
          servers.map((s) => (
            <div
              key={s.name}
              className="p-3 rounded-xl flex items-center justify-between gap-3 text-xs"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
            >
              <div className="flex flex-col gap-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm" style={{ color: 'var(--color-text)' }}>
                    {s.name}
                  </span>
                  <span
                    className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full"
                    style={{
                      background: s.connected ? 'rgba(34, 197, 94, 0.15)' : 'var(--color-bg-tertiary)',
                      color: s.connected ? 'var(--color-success)' : 'var(--color-text-tertiary)',
                    }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        background: s.connected ? 'var(--color-success)' : 'var(--color-text-tertiary)',
                      }}
                    />
                    {s.connected ? 'Connected' : 'Configured'}
                  </span>
                  {s.command && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}>
                      stdio: {s.command} {s.args ? s.args.join(' ') : ''}
                    </span>
                  )}
                  {s.url && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}>
                      sse: {s.url}
                    </span>
                  )}
                </div>
                {s.tools && s.tools.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    <span className="text-[10px]" style={{ color: 'var(--color-text-tertiary)' }}>Tools:</span>
                    {s.tools.map((t) => (
                      <span
                        key={t}
                        className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                        style={{ background: 'var(--color-surface)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleDeleteServer(s.name)}
                className="p-1.5 rounded-lg transition-colors hover:text-red-400 cursor-pointer shrink-0"
                style={{ color: 'var(--color-text-tertiary)' }}
                title="Remove server"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))
        )}
      </div>

      {/* Plugins Section */}
      <SettingRow
        label="Installed Plugins"
        description="Local plugins loaded from plugins/ or ~/.nivos/plugins"
      >
        <button
          type="button"
          onClick={handleReloadPlugins}
          disabled={reloading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
        >
          <RefreshCw size={12} className={reloading ? 'animate-spin' : ''} />
          {reloading ? 'Reloading...' : 'Reload Plugins'}
        </button>
      </SettingRow>

      <div className="flex flex-col gap-2 my-2">
        {plugins.length === 0 ? (
          <div className="text-xs py-2 px-3 rounded-lg" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-tertiary)' }}>
            No external plugins discovered. Plugins placed in the <code className="font-mono">plugins/</code> folder are auto-loaded here.
          </div>
        ) : (
          plugins.map((p) => (
            <div
              key={p.name}
              className="p-3 rounded-xl flex items-center justify-between gap-3 text-xs"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
            >
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm" style={{ color: 'var(--color-text)' }}>
                    {p.name}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}>
                    v{p.version}
                  </span>
                  {p.error && (
                    <span className="text-[10px] text-red-400">({p.error})</span>
                  )}
                </div>
                {p.description && (
                  <p className="text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>
                    {p.description}
                  </p>
                )}
                {p.tools && p.tools.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    <span className="text-[10px]" style={{ color: 'var(--color-text-tertiary)' }}>Provided tools:</span>
                    {p.tools.map((t) => (
                      <span
                        key={t}
                        className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                        style={{ background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </Section>
  );
}

function ObsidianVaultSection() {
  const [vaultConfig, setVaultConfig] = useState<VaultConfig | null>(null);
  const [vaultPath, setVaultPath] = useState('');
  const [allowCloud, setAllowCloud] = useState(false);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await fetchVaultConfig();
      setVaultConfig(cfg);
      setVaultPath(cfg.vault_path);
      setAllowCloud(cfg.allow_obsidian_to_cloud);
    } catch {
      // Ignored
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const handleSavePath = async () => {
    setLoading(true);
    try {
      const updated = await saveVaultConfig({ vault_path: vaultPath });
      setVaultConfig(updated);
      toast.success('Obsidian vault path saved');
    } catch (err: any) {
      toast.error(err.message || 'Failed to save vault path');
    } finally {
      setLoading(false);
    }
  };

  const handleToggleCloud = async () => {
    const next = !allowCloud;
    setAllowCloud(next);
    try {
      const updated = await saveVaultConfig({ allow_obsidian_to_cloud: next });
      setVaultConfig(updated);
      toast.success(next ? 'Vault notes allowed to cloud models' : 'Vault notes kept strictly local');
    } catch (err: any) {
      setAllowCloud(!next);
      toast.error(err.message || 'Failed to update privacy setting');
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await syncVault();
      toast.success(res.message || 'Vault indexed successfully');
    } catch (err: any) {
      toast.error(err.message || 'Vault sync failed');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <Section title="Obsidian Vault">
      <SettingRow
        label="Vault path"
        description="Local directory containing your markdown notes (read-only indexing)"
      >
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
            placeholder="C:\Nivos\NIVOS-vault"
            className="text-xs px-2.5 py-1.5 rounded-lg outline-none w-64"
            style={{
              background: 'var(--color-bg-secondary)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            }}
          />
          <button
            type="button"
            onClick={handleSavePath}
            disabled={loading}
            className="text-xs px-3 py-1.5 rounded-lg font-medium cursor-pointer"
            style={{
              background: 'var(--color-accent)',
              color: '#fff',
            }}
          >
            Save
          </button>
        </div>
      </SettingRow>
      <SettingRow
        label="Vault status"
        description={
          vaultConfig?.connected
            ? 'Connected and accessible'
            : 'Directory not found or disconnected'
        }
      >
        <div className="flex items-center gap-2">
          <span
            className="px-2 py-0.5 rounded text-xs font-medium flex items-center gap-1.5"
            style={{
              background: vaultConfig?.connected
                ? 'rgba(34, 197, 94, 0.15)'
                : 'rgba(239, 68, 68, 0.15)',
              color: vaultConfig?.connected
                ? 'var(--color-success, #22c55e)'
                : 'var(--color-error, #ef4444)',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: vaultConfig?.connected
                  ? 'var(--color-success, #22c55e)'
                  : 'var(--color-error, #ef4444)',
              }}
            />
            {vaultConfig?.connected ? 'Connected' : 'Disconnected'}
          </span>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing || !vaultConfig?.connected}
            className="text-xs px-2.5 py-1 rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-bg-tertiary)] flex items-center gap-1 cursor-pointer disabled:opacity-50"
            style={{ color: 'var(--color-text)' }}
          >
            <RefreshCw className={`w-3 h-3 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? 'Indexing...' : 'Re-index'}
          </button>
        </div>
      </SettingRow>
      <SettingRow
        label="Allow cloud models"
        description="Allow vault note content to be sent to cloud models (OpenAI, Anthropic, Gemini, etc.)"
      >
        <button
          type="button"
          onClick={handleToggleCloud}
          className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
          style={{
            background: allowCloud ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
          }}
        >
          <span
            className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
            style={{
              transform: allowCloud ? 'translateX(20px)' : 'translateX(0)',
              boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
            }}
          />
        </button>
      </SettingRow>
    </Section>
  );
}

function MemoryReviewSection() {
  const [proposals, setProposals] = useState<VaultProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [actioning, setActioning] = useState<string | null>(null);

  const loadProposals = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchVaultProposals();
      setProposals(res.proposals || []);
    } catch {
      // Ignored
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProposals();
  }, [loadProposals]);

  const handleApprove = async (filename: string) => {
    setActioning(filename);
    try {
      const res = await approveVaultProposal(filename);
      toast.success(res.message || 'Proposal approved into Nivos-Memory');
      await loadProposals();
    } catch (err: any) {
      toast.error(err.message || 'Failed to approve proposal');
    } finally {
      setActioning(null);
    }
  };

  const handleReject = async (filename: string) => {
    setActioning(filename);
    try {
      const res = await rejectVaultProposal(filename);
      toast.success(res.message || 'Proposal rejected');
      await loadProposals();
    } catch (err: any) {
      toast.error(err.message || 'Failed to reject proposal');
    } finally {
      setActioning(null);
    }
  };

  return (
    <Section title="Memory Review">
      <div className="py-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
        Proposals saved to <code className="px-1 py-0.5 rounded bg-[var(--color-bg-tertiary)]">Nivos-Inbox/</code> await review before promotion into long-term <code className="px-1 py-0.5 rounded bg-[var(--color-bg-tertiary)]">Nivos-Memory/</code>.
      </div>

      {loading && proposals.length === 0 ? (
        <div className="py-4 text-center text-xs text-[var(--color-text-tertiary)]">
          Loading proposals...
        </div>
      ) : proposals.length === 0 ? (
        <div className="py-4 px-3 rounded-lg border border-[var(--color-border)] text-center text-xs text-[var(--color-text-tertiary)] bg-[var(--color-bg-secondary)]">
          No pending memory proposals in Nivos-Inbox.
        </div>
      ) : (
        <div className="flex flex-col gap-2 mt-2">
          {proposals.map((p) => (
            <div
              key={p.filename}
              className="p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] flex flex-col gap-2"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-[var(--color-text)]">
                    {p.title}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] font-mono">
                    {p.filename}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[rgba(59,130,246,0.15)] text-[var(--color-accent)] font-medium">
                    {p.proposal_type}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => handleApprove(p.filename)}
                    disabled={actioning === p.filename}
                    className="text-xs px-2.5 py-1 rounded-md bg-[var(--color-success,#22c55e)] hover:opacity-90 text-white font-medium flex items-center gap-1 cursor-pointer disabled:opacity-50"
                  >
                    <Check className="w-3 h-3" />
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => handleReject(p.filename)}
                    disabled={actioning === p.filename}
                    className="text-xs px-2.5 py-1 rounded-md bg-[var(--color-error,#ef4444)] hover:opacity-90 text-white font-medium flex items-center gap-1 cursor-pointer disabled:opacity-50"
                  >
                    <Trash2 className="w-3 h-3" />
                    Reject
                  </button>
                </div>
              </div>
              <div
                className="text-xs p-2 rounded border border-[var(--color-border)] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                {p.snippet}
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function AppsManagementSection() {
  const [settings, setSettings] = useState<AppSettings>({
    open_any_installed_app: true,
    always_ask_apps: ['1password', 'bitwarden', 'keepass', 'lastpass', 'bank', 'remote desktop', 'teamviewer', 'anydesk'],
    allowed_apps: [],
    blocklist: [
      'cmd',
      'powershell',
      'pwsh',
      'bash',
      'sh',
      'wsl',
      'wscript',
      'cscript',
      'mshta',
      'rundll32',
      'regedit',
      'msiexec',
      'installers (*.msi, *setup*)',
    ],
  });
  const [discovered, setDiscovered] = useState<DiscoveredApp[]>([]);
  const [searchFilter, setSearchFilter] = useState('');
  const [newAsk, setNewAsk] = useState('');
  const [newAllowed, setNewAllowed] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [s, d] = await Promise.all([
        fetchAppSettings().catch(() => null),
        fetchDiscoveredApps().catch(() => ({ installed_apps: [], total: 0 })),
      ]);
      if (s) setSettings(s);
      if (d) setDiscovered(d.installed_apps || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleToggleOpenAny = async () => {
    try {
      setSaving(true);
      const next = !settings.open_any_installed_app;
      const res = await updateAppSettings({ open_any_installed_app: next });
      setSettings((prev) => ({ ...prev, open_any_installed_app: res.open_any_installed_app }));
      toast.success(next ? 'Enabled opening any installed app.' : 'Disabled opening any installed app.');
    } catch (err: any) {
      toast.error(err.message || 'Failed to update setting');
    } finally {
      setSaving(false);
    }
  };

  const handleAddAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = newAsk.trim().toLowerCase();
    if (!clean || settings.always_ask_apps.includes(clean)) return;
    const nextList = [...settings.always_ask_apps, clean];
    try {
      setSaving(true);
      const res = await updateAppSettings({ always_ask_apps: nextList });
      setSettings((prev) => ({ ...prev, always_ask_apps: res.always_ask_apps }));
      setNewAsk('');
      toast.success(`Added '${clean}' to always ask list.`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveAsk = async (item: string) => {
    const nextList = settings.always_ask_apps.filter((a) => a !== item);
    try {
      const res = await updateAppSettings({ always_ask_apps: nextList });
      setSettings((prev) => ({ ...prev, always_ask_apps: res.always_ask_apps }));
      toast.success(`Removed '${item}' from always ask list.`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to remove');
    }
  };

  const handleAddAllowed = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = newAllowed.trim().toLowerCase();
    if (!clean || settings.allowed_apps.includes(clean)) return;
    const nextList = [...settings.allowed_apps, clean];
    try {
      setSaving(true);
      const res = await updateAppSettings({ allowed_apps: nextList });
      setSettings((prev) => ({ ...prev, allowed_apps: res.allowed_apps }));
      setNewAllowed('');
      toast.success(`Added '${clean}' to pre-approved apps.`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveAllowed = async (item: string) => {
    const nextList = settings.allowed_apps.filter((a) => a !== item);
    try {
      const res = await updateAppSettings({ allowed_apps: nextList });
      setSettings((prev) => ({ ...prev, allowed_apps: res.allowed_apps }));
      toast.success(`Removed '${item}' from pre-approved apps.`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to remove');
    }
  };

  const filteredDiscovered = discovered.filter((a) =>
    a.name.toLowerCase().includes(searchFilter.trim().toLowerCase())
  );

  return (
    <Section title="Applications & Launch Controls">
      {/* Switch: Let nivos open any installed app */}
      <SettingRow
        label="Let nivos open any installed app"
        description="When enabled, nivos can open safe discovered applications directly from local chat turns. Apps matching the 'Always ask first' list will still require your confirmation."
      >
        <button
          type="button"
          disabled={saving || loading}
          onClick={handleToggleOpenAny}
          className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer disabled:opacity-40"
          style={{
            background: settings.open_any_installed_app ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <span
            className="inline-block h-3.5 w-3.5 rounded-full transition-transform"
            style={{
              background: 'white',
              transform: settings.open_any_installed_app ? 'translateX(18px)' : 'translateX(2px)',
            }}
          />
        </button>
      </SettingRow>

      {/* System Blocklist */}
      <div className="py-3 border-b border-[var(--color-border-subtle)]">
        <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-error)' }}>
          System Blocklist (Always Blocked)
        </div>
        <div className="text-[11px] text-[var(--color-text-tertiary)] mb-2">
          Shells, script interpreters, registry tools, installers, and administrative executables can never be launched by nivos.
        </div>
        <div className="flex flex-wrap gap-1.5">
          {settings.blocklist.map((b) => (
            <span
              key={b}
              className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono bg-red-500/10 text-red-400 border border-red-500/20"
            >
              {b}
            </span>
          ))}
        </div>
      </div>

      {/* Always ask first */}
      <div className="py-3 border-b border-[var(--color-border-subtle)]">
        <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-text)' }}>
          Always Ask First
        </div>
        <div className="text-[11px] text-[var(--color-text-tertiary)] mb-2">
          Even with master open-app enabled, apps matching these keywords (password managers, banking, remote desktop) always require explicit approval.
        </div>
        <div className="flex flex-wrap gap-1.5 mb-2">
          {settings.always_ask_apps.map((item) => (
            <span
              key={item}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs"
              style={{
                background: 'rgba(234, 179, 8, 0.1)',
                border: '1px solid rgba(234, 179, 8, 0.3)',
                color: 'var(--color-text)',
              }}
            >
              <span className="font-mono">{item}</span>
              <button
                type="button"
                onClick={() => handleRemoveAsk(item)}
                className="hover:text-red-400 cursor-pointer ml-0.5"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
        <form onSubmit={handleAddAsk} className="flex items-center gap-2">
          <input
            type="text"
            value={newAsk}
            onChange={(e) => setNewAsk(e.target.value)}
            placeholder="Add keyword e.g. bitwarden, vpn"
            className="text-xs px-2.5 py-1.5 rounded-lg outline-none w-56 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)]"
          />
          <button
            type="submit"
            disabled={saving || !newAsk.trim()}
            className="text-xs px-3 py-1.5 rounded-lg font-medium cursor-pointer disabled:opacity-50 bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]"
          >
            Add
          </button>
        </form>
      </div>

      {/* Pre-approved allow list (if open_any_installed_app is false or for specific overrides) */}
      {!settings.open_any_installed_app && (
        <div className="py-3 border-b border-[var(--color-border-subtle)]">
          <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-text)' }}>
            Pre-Approved Applications (Allow List)
          </div>
          <div className="text-[11px] text-[var(--color-text-tertiary)] mb-2">
            Since opening any installed app is turned off, only applications listed here can open without prompt.
          </div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {settings.allowed_apps.length === 0 ? (
              <span className="text-xs italic text-[var(--color-text-tertiary)]">No pre-approved apps.</span>
            ) : (
              settings.allowed_apps.map((item) => (
                <span
                  key={item}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)]"
                >
                  {item}
                  <button
                    type="button"
                    onClick={() => handleRemoveAllowed(item)}
                    className="hover:text-red-400 cursor-pointer ml-0.5"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))
            )}
          </div>
          <form onSubmit={handleAddAllowed} className="flex items-center gap-2">
            <input
              type="text"
              value={newAllowed}
              onChange={(e) => setNewAllowed(e.target.value)}
              placeholder="e.g. spotify, obsidian"
              className="text-xs px-2.5 py-1.5 rounded-lg outline-none w-56 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)]"
            />
            <button
              type="submit"
              disabled={saving || !newAllowed.trim()}
              className="text-xs px-3 py-1.5 rounded-lg font-medium cursor-pointer disabled:opacity-50 bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]"
            >
              Add
            </button>
          </form>
        </div>
      )}

      {/* Discovered Applications on this PC */}
      <div className="pt-3">
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className="text-xs font-semibold" style={{ color: 'var(--color-text)' }}>
              Auto-Discovered Installed Apps ({discovered.length})
            </div>
            <div className="text-[11px] text-[var(--color-text-tertiary)]">
              Discovered from Windows Start Menu programs and desktop shortcuts.
            </div>
          </div>
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Search discovered..."
            className="text-xs px-2 py-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] w-44"
          />
        </div>

        <div className="max-h-48 overflow-y-auto rounded-lg border border-[var(--color-border)] p-2 bg-[var(--color-bg-secondary)]">
          {filteredDiscovered.length === 0 ? (
            <div className="text-xs text-[var(--color-text-tertiary)] py-2 text-center">
              {discovered.length === 0 ? 'Scanning applications...' : 'No applications match filter.'}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {filteredDiscovered.map((app) => (
                <span
                  key={app.name}
                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]"
                  title={app.path || app.exe || app.name}
                >
                  <span className="font-medium">{app.name}</span>
                  <span className="text-[9px] uppercase px-1 py-0.2 rounded bg-[var(--color-bg-tertiary)] text-[var(--color-text-tertiary)]">
                    {app.source === 'start_menu' ? 'Start Menu' : app.source === 'windows_builtin' ? 'Built-in' : 'Installed'}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* winget install_app note */}
        <div className="text-[11px] text-[var(--color-text-tertiary)] mt-3 p-2.5 rounded bg-[var(--color-surface)] border border-[var(--color-border)]">
          <span className="font-semibold text-[var(--color-text)]">App Installation:</span> Powered by the <code className="font-mono px-1 py-0.5 rounded bg-[var(--color-bg-secondary)]">install_app</code> tool via winget exact package IDs (e.g. <code className="font-mono">Spotify.Spotify</code>, <code className="font-mono">Notion.Notion</code>). Always displays publisher and version details and asks for approval once per package before running.
        </div>
      </div>
    </Section>
  );
}

function SandboxSettingsSection() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [startingDocker, setStartingDocker] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [prepareProgress, setPrepareProgress] = useState<{
    status: 'idle' | 'preparing' | 'ready' | 'error';
    progress: number;
    step: string;
    error?: string;
  } | null>(null);
  const [permissions, setPermissions] = useState<{ allow: string[]; deny: string[] }>({
    allow: [],
    deny: [],
  });
  const [newAllow, setNewAllow] = useState('');
  const [newDeny, setNewDeny] = useState('');
  const [savingPerms, setSavingPerms] = useState(false);
  const [msg, setMsg] = useState('');

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetchSandboxStatus();
      setStatus(res);
      if (res.prepare_status) {
        setPrepareProgress(res.prepare_status);
        if (res.prepare_status.status === 'preparing') {
          setPreparing(true);
        }
      }
    } catch {
      // ignore
    }
  }, []);

  const loadPermissions = useCallback(async () => {
    try {
      const perms = await fetchSandboxPermissions();
      setPermissions(perms);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadStatus();
    loadPermissions();
  }, [loadStatus, loadPermissions]);

  // Poll prepare progress if active
  useEffect(() => {
    let timer: any = null;
    if (preparing) {
      timer = setInterval(async () => {
        try {
          const res = await fetchPrepareStatus();
          setPrepareProgress(res);
          if (res.status === 'ready' || res.status === 'error') {
            setPreparing(false);
            await loadStatus();
          }
        } catch {
          // ignore
        }
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [preparing, loadStatus]);

  const handleStartDocker = async () => {
    try {
      setStartingDocker(true);
      const res = await startDockerDesktop();
      setMsg(res.message);
      setTimeout(() => loadStatus(), 3000);
      setTimeout(() => setMsg(''), 5000);
    } catch (e: any) {
      setMsg(`Start Docker failed: ${e.message}`);
    } finally {
      setStartingDocker(false);
    }
  };

  const handlePrepareSandbox = async () => {
    try {
      setPreparing(true);
      const res = await prepareSandbox();
      setPrepareProgress(res as any);
      setMsg('Preparing sandbox container and package volume...');
      setTimeout(() => setMsg(''), 4000);
    } catch (e: any) {
      setPreparing(false);
      setMsg(`Prepare failed: ${e.message}`);
    }
  };

  const handleTogglePersistentAutoApprove = async () => {
    if (!status) return;
    try {
      setLoading(true);
      const next = !status.auto_approve_persistent;
      const res = await updateSandboxPersistentAutoApprove(next);
      setStatus((prev) => (prev ? { ...prev, auto_approve_persistent: res.auto_approve_persistent } : null));
      setMsg(res.auto_approve_persistent ? 'Persistent sandbox auto-approve enabled.' : 'Persistent sandbox auto-approve disabled.');
      setTimeout(() => setMsg(''), 3000);
    } catch (e: any) {
      setMsg(`Failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    try {
      setResetting(true);
      const res = await resetSandbox();
      setMsg(res.message);
      await loadStatus();
      setTimeout(() => setMsg(''), 3000);
    } catch (e: any) {
      setMsg(`Reset failed: ${e.message}`);
    } finally {
      setResetting(false);
    }
  };

  const handleAddAllow = () => {
    const val = newAllow.trim();
    if (!val || permissions.allow.includes(val)) return;
    setPermissions((p) => ({ ...p, allow: [...p.allow, val] }));
    setNewAllow('');
  };

  const handleRemoveAllow = (pattern: string) => {
    setPermissions((p) => ({ ...p, allow: p.allow.filter((x) => x !== pattern) }));
  };

  const handleAddDeny = () => {
    const val = newDeny.trim();
    if (!val || permissions.deny.includes(val)) return;
    setPermissions((p) => ({ ...p, deny: [...p.deny, val] }));
    setNewDeny('');
  };

  const handleRemoveDeny = (pattern: string) => {
    setPermissions((p) => ({ ...p, deny: p.deny.filter((x) => x !== pattern) }));
  };

  const handleSavePermissions = async () => {
    try {
      setSavingPerms(true);
      const updated = await updateSandboxPermissions(permissions);
      setPermissions(updated);
      setMsg('Permissions saved.');
      setTimeout(() => setMsg(''), 3000);
    } catch (e: any) {
      setMsg(`Failed: ${e.message}`);
    } finally {
      setSavingPerms(false);
    }
  };

  return (
    <Section title="Docker Sandbox & Execution">
      {msg && (
        <div className="text-xs p-2 rounded mb-3 text-emerald-400" style={{ background: 'rgba(16, 185, 129, 0.1)' }}>
          {msg}
        </div>
      )}

      {/* Status Wizard Card */}
      <div
        className="p-3.5 rounded-lg mb-4"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold" style={{ color: 'var(--color-text)' }}>
              Sandbox Environment Wizard
            </span>
          </div>
          <span
            className="text-[11px] font-medium px-2 py-0.5 rounded-full"
            style={{
              background: status?.docker_available ? 'rgba(16, 185, 129, 0.15)' : 'rgba(234, 179, 8, 0.15)',
              color: status?.docker_available ? 'var(--color-success)' : '#eab308',
            }}
          >
            {status?.docker_available ? (status.container_running ? 'Docker Active' : 'Docker Ready') : 'Host Fallback'}
          </span>
        </div>

        {/* Wizard Step Badges */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
          {/* Step 1: Installed */}
          <div
            className="p-2.5 rounded border text-xs"
            style={{
              background: 'var(--color-surface)',
              borderColor: status?.docker_installed ? 'rgba(16, 185, 129, 0.3)' : 'var(--color-border)',
            }}
          >
            <div className="text-[10px] uppercase font-bold text-[var(--color-text-tertiary)]">1. Docker CLI</div>
            <div className="font-medium mt-0.5" style={{ color: status?.docker_installed ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
              {status?.docker_installed ? `Installed (v${status.docker_version})` : 'Not Found'}
            </div>
          </div>

          {/* Step 2: Daemon */}
          <div
            className="p-2.5 rounded border text-xs"
            style={{
              background: 'var(--color-surface)',
              borderColor: status?.docker_running ? 'rgba(16, 185, 129, 0.3)' : 'var(--color-border)',
            }}
          >
            <div className="text-[10px] uppercase font-bold text-[var(--color-text-tertiary)]">2. Engine / Daemon</div>
            <div className="font-medium mt-0.5" style={{ color: status?.docker_running ? 'var(--color-success)' : '#eab308' }}>
              {status?.docker_running ? 'Running' : 'Stopped'}
            </div>
          </div>

          {/* Step 3: WSL2 */}
          <div
            className="p-2.5 rounded border text-xs"
            style={{
              background: 'var(--color-surface)',
              borderColor: status?.wsl?.wslconfig_found ? 'rgba(16, 185, 129, 0.3)' : 'var(--color-border)',
            }}
          >
            <div className="text-[10px] uppercase font-bold text-[var(--color-text-tertiary)]">3. WSL2 Memory</div>
            <div className="font-medium mt-0.5" style={{ color: status?.wsl?.wslconfig_found ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
              {status?.wsl?.wsl_installed
                ? status.wsl.wslconfig_found
                  ? `Capped (${status.wsl.wslconfig_memory})`
                  : 'Uncapped (.wslconfig)'
                : 'Not Detected'}
            </div>
          </div>
        </div>

        {status?.wsl?.guidance && !status.wsl.wslconfig_found && (
          <div className="text-[11px] p-2 rounded mb-3 bg-amber-500/10 text-amber-300 font-mono">
            {status.wsl.guidance}
          </div>
        )}

        {/* Wizard Action Buttons */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {!status?.docker_running && (
            <button
              type="button"
              disabled={startingDocker}
              onClick={handleStartDocker}
              className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors disabled:opacity-50"
              style={{
                background: 'var(--color-accent)',
                color: 'white',
              }}
            >
              {startingDocker ? 'Starting Docker Desktop...' : 'Start Docker Desktop'}
            </button>
          )}

          <button
            type="button"
            disabled={preparing || !status?.docker_installed}
            onClick={handlePrepareSandbox}
            className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors disabled:opacity-50"
            style={{
              background: 'var(--color-surface)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            }}
          >
            {preparing ? 'Preparing Sandbox...' : 'Prepare Sandbox'}
          </button>

          <button
            type="button"
            disabled={resetting || !status?.docker_available}
            onClick={handleReset}
            className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors disabled:opacity-50 ml-auto"
            style={{
              background: 'var(--color-surface)',
              color: 'var(--color-error)',
              border: '1px solid var(--color-error)',
            }}
          >
            {resetting ? 'Resetting...' : 'Reset Sandbox'}
          </button>
        </div>

        {/* Prepare Progress Bar */}
        {prepareProgress && prepareProgress.status !== 'idle' && (
          <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
            <div className="flex justify-between text-xs mb-1">
              <span className="font-medium" style={{ color: 'var(--color-text)' }}>
                {prepareProgress.step}
              </span>
              <span className="text-[var(--color-text-tertiary)]">{prepareProgress.progress}%</span>
            </div>
            <div className="w-full bg-[var(--color-surface)] h-2 rounded-full overflow-hidden border border-[var(--color-border)]">
              <div
                className="h-full bg-emerald-500 transition-all duration-300"
                style={{ width: `${prepareProgress.progress}%` }}
              />
            </div>
            {prepareProgress.error && (
              <div className="text-[11px] text-red-400 mt-1">{prepareProgress.error}</div>
            )}
          </div>
        )}
      </div>

      {/* Persistent Auto-Approve Toggle */}
      <SettingRow
        label="Auto-approve inside sandbox (persistent)"
        description="Allow commands to run inside Docker without confirmation. Applies ONLY to sandbox commands typed in local chat; never applies to host, messaging channels, or scheduled tasks."
      >
        <button
          type="button"
          disabled={loading || !status?.docker_available}
          onClick={handleTogglePersistentAutoApprove}
          className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer disabled:opacity-40"
          style={{
            background: status?.auto_approve_persistent ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <span
            className="inline-block h-3.5 w-3.5 rounded-full transition-transform"
            style={{
              background: 'white',
              transform: status?.auto_approve_persistent ? 'translateX(18px)' : 'translateX(2px)',
            }}
          />
        </button>
      </SettingRow>

      {/* Installed in Sandbox (Named Volume & Packages) */}
      <div className="py-3.5 border-b border-[var(--color-border-subtle)]">
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
              Installed in Sandbox
            </div>
            <div className="text-xs text-[var(--color-text-tertiary)]">
              Packages persist in named volume <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-[var(--color-surface)]">{status?.named_volume || 'nivos-sandbox-packages'}</code>
            </div>
          </div>
          <div className="text-right">
            <span className="text-xs font-mono font-medium" style={{ color: 'var(--color-text)' }}>
              {status?.volume_size_mb ?? 0} MB / {status?.volume_cap_mb ?? 5120} MB
            </span>
            <div className="text-[10px] text-[var(--color-text-tertiary)]">volume size cap</div>
          </div>
        </div>

        {status?.installed_packages && status.installed_packages.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {status.installed_packages.map((pkg) => (
              <span
                key={pkg.name}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text)',
                }}
              >
                <span className="font-medium">{pkg.name}</span>
                {pkg.version && <span className="text-[var(--color-text-tertiary)] text-[10px]">v{pkg.version}</span>}
                <span className="text-[9px] uppercase font-bold tracking-wider px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-400">
                  installed in sandbox
                </span>
              </span>
            ))}
          </div>
        ) : (
          <div className="text-xs italic text-[var(--color-text-tertiary)] mt-1">
            No custom packages installed yet. Commands like <code className="font-mono text-[11px]">pip install pandas</code> run in sandbox persist here.
          </div>
        )}
      </div>

      {/* Permissions Allow / Deny Pattern Editor */}
      <div className="pt-4">
        <div className="text-sm font-medium mb-1" style={{ color: 'var(--color-text)' }}>
          Sandbox Command Permissions
        </div>
        <div className="text-xs text-[var(--color-text-tertiary)] mb-3">
          Configure command patterns that are auto-approved or strictly blocked inside the sandbox.
        </div>

        {/* Allow Patterns */}
        <div className="mb-4">
          <div className="text-xs font-semibold text-emerald-400 mb-1">
            Allowed Patterns (Auto-approved in sandbox)
          </div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {permissions.allow.map((pat) => (
              <span
                key={pat}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs"
                style={{
                  background: 'rgba(16, 185, 129, 0.1)',
                  border: '1px solid rgba(16, 185, 129, 0.3)',
                  color: 'var(--color-text)',
                }}
              >
                <code className="font-mono text-[11px]">{pat}</code>
                <button
                  type="button"
                  onClick={() => handleRemoveAllow(pat)}
                  className="hover:text-red-400 text-[var(--color-text-tertiary)]"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="e.g. pip install *"
              value={newAllow}
              onChange={(e) => setNewAllow(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleAddAllow();
                }
              }}
              className="flex-1 px-2.5 py-1 text-xs rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)]"
            />
            <button
              type="button"
              onClick={handleAddAllow}
              className="px-2.5 py-1 text-xs rounded bg-[var(--color-surface)] border border-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] text-[var(--color-text)]"
            >
              Add
            </button>
          </div>
        </div>

        {/* Deny Patterns */}
        <div className="mb-4">
          <div className="text-xs font-semibold text-red-400 mb-1">
            Denied Patterns (Blocked everywhere)
          </div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {permissions.deny.map((pat) => (
              <span
                key={pat}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs"
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  color: 'var(--color-text)',
                }}
              >
                <code className="font-mono text-[11px]">{pat}</code>
                <button
                  type="button"
                  onClick={() => handleRemoveDeny(pat)}
                  className="hover:text-red-400 text-[var(--color-text-tertiary)]"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="e.g. rm -rf /*"
              value={newDeny}
              onChange={(e) => setNewDeny(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleAddDeny();
                }
              }}
              className="flex-1 px-2.5 py-1 text-xs rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)]"
            />
            <button
              type="button"
              onClick={handleAddDeny}
              className="px-2.5 py-1 text-xs rounded bg-[var(--color-surface)] border border-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] text-[var(--color-text)]"
            >
              Add
            </button>
          </div>
        </div>

        <button
          type="button"
          disabled={savingPerms}
          onClick={handleSavePermissions}
          className="px-3 py-1.5 text-xs font-medium rounded cursor-pointer transition-colors"
          style={{
            background: 'var(--color-accent)',
            color: 'white',
          }}
        >
          {savingPerms ? 'Saving...' : 'Save Permissions'}
        </button>
      </div>
    </Section>
  );
}



function ModelRoutingSection() {
  const [routing, setRouting] = useState<RoutingSettings | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRoutingSettings()
      .then(setRouting)
      .finally(() => setLoading(false));
  }, []);

  const update = async (patch: Partial<RoutingSettings>) => {
    if (!routing) return;
    const next = { ...routing, ...patch };
    setRouting(next);
    try {
      await saveRoutingSettings(patch);
      toast.success('Routing settings updated');
    } catch (e: any) {
      toast.error('Failed to update routing: ' + e.message);
    }
  };

  if (loading || !routing) return null;

  return (
    <Section title="Model Routing Tiers">
      <SettingRow label="Fast tier (Greetings & Casual Chat)" description="Smallest model for fast replies without tool overhead">
        <input
          type="text"
          value={routing.fast}
          onChange={(e) => update({ fast: e.target.value })}
          className="px-2 py-1 rounded text-xs w-48 font-mono border"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
        />
      </SettingRow>
      <SettingRow label="Main tier (General & Complex)" description="Main assistant model with tools and pre-fetched live data">
        <input
          type="text"
          value={routing.main}
          onChange={(e) => update({ main: e.target.value })}
          className="px-2 py-1 rounded text-xs w-48 font-mono border"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
        />
      </SettingRow>
      <SettingRow label="Code tier (Programming)" description="Specialized model for code, syntax, scripts, and debugging">
        <input
          type="text"
          value={routing.code}
          onChange={(e) => update({ code: e.target.value })}
          className="px-2 py-1 rounded text-xs w-48 font-mono border"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
        />
      </SettingRow>
      <SettingRow label="Deep tier (Heavy Reasoning)" description="Optional larger model for manual selection (e.g. qwen3.5:9b)">
        <input
          type="text"
          value={routing.deep}
          onChange={(e) => update({ deep: e.target.value })}
          className="px-2 py-1 rounded text-xs w-48 font-mono border"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
        />
      </SettingRow>
      <SettingRow label="Embeddings model" description="Local embedding model for semantic memory and indexing">
        <input
          type="text"
          value={routing.embeddings}
          onChange={(e) => update({ embeddings: e.target.value })}
          className="px-2 py-1 rounded text-xs w-48 font-mono border"
          style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
        />
      </SettingRow>
      <SettingRow label="Always search the web" description="Search the web for every question (pre-fetches results)">
        <button
          onClick={() => update({ always_search: !routing.always_search })}
          className={`w-11 h-6 flex items-center rounded-full p-1 transition-colors cursor-pointer ${
            routing.always_search ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-bg-tertiary)]'
          }`}
        >
          <div
            className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${
              routing.always_search ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </SettingRow>
    </Section>
  );
}

function LocationContextSection() {
  const [locInput, setLocInput] = useState('');
  const [tzInput, setTzInput] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getContextSettings()
      .then((c) => {
        setLocInput(c.location);
        setTzInput(c.timezone);
      })
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    try {
      await saveContextSettings({ location: locInput, timezone: tzInput });
      toast.success('Location & Timezone saved');
    } catch (e: any) {
      toast.error('Failed to save context: ' + e.message);
    }
  };

  if (loading) return null;

  return (
    <Section title="Location & Real-Time Context">
      <SettingRow label="My location" description="Default location for weather and local queries (no IP lookup is performed)">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={locInput}
            onChange={(e) => setLocInput(e.target.value)}
            className="px-2 py-1 rounded text-xs w-56 border"
            style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
            placeholder="City, State, Country"
          />
          <button
            onClick={save}
            className="px-2.5 py-1 rounded text-xs font-medium cursor-pointer"
            style={{ background: 'var(--color-accent)', color: '#fff' }}
          >
            Save
          </button>
        </div>
      </SettingRow>
      <SettingRow label="Timezone" description="Timezone used to compute the local date/time injected into the system prompt">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={tzInput}
            onChange={(e) => setTzInput(e.target.value)}
            className="px-2 py-1 rounded text-xs w-56 border font-mono"
            style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', borderColor: 'var(--color-border)' }}
            placeholder="e.g. Asia/Kolkata"
          />
          <button
            onClick={save}
            className="px-2.5 py-1 rounded text-xs font-medium cursor-pointer"
            style={{ background: 'var(--color-accent)', color: '#fff' }}
          >
            Save
          </button>
        </div>
      </SettingRow>
    </Section>
  );
}

const themeOptions: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
];

export function SettingsPage() {
  const settings = useAppStore((s) => s.settings);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const conversations = useAppStore((s) => s.conversations);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [speechBackendAvailable, setSpeechBackendAvailable] = useState<boolean | null>(null);
  const [ttsBackend, setTtsBackend] = useState<TtsHealth | null>(null);
  const [selectedVoice, setSelectedVoice] = useState<string>('bm_george');
  const [testingVoice, setTestingVoice] = useState(false);
  const [testVoiceError, setTestVoiceError] = useState<string | null>(null);
  const testAudioRef = useRef<HTMLAudioElement | null>(null);
  const [saved, setSaved] = useState(false);

  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState(() => !isAutoUpdateDisabled());
  const [updateCheckState, setUpdateCheckState] = useState<'idle' | 'checking' | 'available' | 'latest'>('idle');

  const handleAutoUpdateToggle = useCallback((enabled: boolean) => {
    setAutoUpdateEnabled(enabled);
    setAutoUpdateDisabled(!enabled);
  }, []);

  const handleCheckNow = useCallback(async () => {
    if (!(window as any).__TAURI_INTERNALS__) return;
    setUpdateCheckState('checking');
    try {
      const { check } = await import('@tauri-apps/plugin-updater');
      const update = await check();
      setUpdateCheckState(update ? 'available' : 'latest');
      setTimeout(() => setUpdateCheckState('idle'), 4000);
    } catch {
      setUpdateCheckState('idle');
    }
  }, []);

  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus>({ kind: 'loading' });
  const memoryRequestId = useRef(0);
  const [memoryEnabled, setMemoryEnabled] = useState(() => {
    try { return localStorage.getItem('nivos-memory-enabled') !== 'false'; } catch { return true; }
  });
  const [memoryBackend, setMemoryBackend] = useState(() => {
    try { return localStorage.getItem('nivos-memory-backend') || 'sqlite'; } catch { return 'sqlite'; }
  });
  const [memoryTopK, setMemoryTopK] = useState(() => {
    try { return parseInt(localStorage.getItem('nivos-memory-top-k') || '5'); } catch { return 5; }
  });
  const [memoryMinScore, setMemoryMinScore] = useState(() => {
    try { return parseFloat(localStorage.getItem('nivos-memory-min-score') || '0.1'); } catch { return 0.1; }
  });
  const [memoryMaxTokens, setMemoryMaxTokens] = useState(() => {
    try { return parseInt(localStorage.getItem('nivos-memory-max-tokens') || '2048'); } catch { return 2048; }
  });

  const [srcKind, setSrcKind] = useState<InferenceSource['kind']>('ollama');
  const [customHost, setCustomHost] = useState('http://localhost:1234/v1');
  const [customModel, setCustomModel] = useState('');
  const [customEngine, setCustomEngine] = useState('lmstudio');
  const [customKey, setCustomKey] = useState('');
  const [srcMsg, setSrcMsg] = useState('');
  const [allowObsidianToCloud, setAllowObsidianToCloud] = useState(false);

  useEffect(() => {
    getPrivacySettings().then((p) => {
      setAllowObsidianToCloud(p.allow_obsidian_to_cloud);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    getInferenceSource().then((s) => {
      setSrcKind(s.kind);
      if (s.host) setCustomHost(s.host);
      if (s.model) setCustomModel(s.model);
      if (s.engine) setCustomEngine(s.engine);
    }).catch(() => {});
  }, []);

  const saveSource = useCallback(async () => {
    try {
      if (srcKind === 'custom') {
        await setInferenceSource({ kind: 'custom', host: customHost, model: customModel, engine: customEngine, apiKey: customKey || undefined });
      } else {
        await setInferenceSource({ kind: 'ollama' });
      }
      setSrcMsg('Saved — restart the app to apply.');
    } catch (e: any) {
      setSrcMsg(e?.message ?? 'Failed to save.');
    }
  }, [srcKind, customHost, customModel, customEngine, customKey]);

  const refreshMemoryStatus = useCallback(async () => {
    const requestId = ++memoryRequestId.current;
    setMemoryStatus({ kind: 'loading' });
    try {
      const stats = await getMemoryStats();
      if (requestId === memoryRequestId.current) {
        setMemoryStatus({ kind: 'ready', stats });
      }
    } catch (error) {
      if (requestId === memoryRequestId.current) {
        setMemoryStatus({
          kind: 'error',
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }, []);

  useEffect(() => {
    void refreshMemoryStatus();
    const onFocus = () => { void refreshMemoryStatus(); };
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
      ++memoryRequestId.current;
    };
  }, [refreshMemoryStatus, settings.apiUrl]);

  useEffect(() => {
    checkHealth().then(setHealthy);
    fetchSpeechHealth()
      .then((h) => setSpeechBackendAvailable(h.available))
      .catch(() => setSpeechBackendAvailable(false));
    fetchTtsHealth()
      .then((h) => {
        setTtsBackend(h);
        if (h.voice_id) setSelectedVoice(h.voice_id);
      })
      .catch(() => setTtsBackend({ available: false }));
    fetchTtsConfig()
      .then((c) => {
        if (c.voice_id) setSelectedVoice(c.voice_id);
        if (c.read_aloud !== undefined) {
          updateSettings({ voiceAutoplay: c.read_aloud });
        }
      })
      .catch(() => {});
  }, [updateSettings]);

  const handleVoiceChange = async (voiceId: string) => {
    setSelectedVoice(voiceId);
    try {
      await updateTtsConfig({ voice_id: voiceId });
      showSaved();
    } catch (err: any) {
      toast.error(`Failed to update voice: ${err.message}`);
    }
  };

  const handleTestVoice = async () => {
    if (testingVoice && testAudioRef.current) {
      testAudioRef.current.pause();
      testAudioRef.current = null;
      setTestingVoice(false);
      return;
    }
    setTestingVoice(true);
    setTestVoiceError(null);
    try {
      const sampleText = "Hello! I am Nivos, your personal AI assistant running locally.";
      const blob = await synthesizeSpeech(sampleText, { voiceId: selectedVoice });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      testAudioRef.current = audio;
      audio.onended = () => {
        setTestingVoice(false);
        testAudioRef.current = null;
        URL.revokeObjectURL(url);
      };
      audio.onerror = () => {
        setTestingVoice(false);
        setTestVoiceError("Playback of test audio failed.");
        testAudioRef.current = null;
        URL.revokeObjectURL(url);
      };
      await audio.play();
    } catch (err: any) {
      setTestingVoice(false);
      setTestVoiceError(err.message || "Failed to synthesize sample audio.");
    }
  };

  const showSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const handleExport = () => {
    const data = localStorage.getItem('nivos-conversations') || '{}';
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nivos-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        try {
          const data = JSON.parse(ev.target?.result as string);
          if (data.version === 1) {
            localStorage.setItem('nivos-conversations', JSON.stringify(data));
            useAppStore.getState().loadConversations();
            showSaved();
          }
        } catch {}
      };
      reader.readAsText(file);
    };
    input.click();
  };

  const [confirmClear, setConfirmClear] = useState(false);
  const handleClear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    localStorage.removeItem('nivos-conversations');
    useAppStore.getState().loadConversations();
    setConfirmClear(false);
    showSaved();
  };

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-2xl mx-auto">
        <header className="mb-6">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>
              Settings
            </h1>
            {saved && (
              <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full" style={{
                background: 'var(--color-accent-subtle)',
                color: 'var(--color-success)',
              }}>
                <Check size={12} /> Saved
              </span>
            )}
          </div>
          <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--color-text-secondary)' }}>
            App preferences — appearance, model defaults, keyboard shortcuts, and data management.
          </p>
        </header>

        <div className="flex flex-col gap-4">
          {/* Appearance */}
          <Section title="Appearance">
            <SettingRow label="Theme" description="Choose how Nivos looks">
              <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--color-bg-secondary)' }}>
                {themeOptions.map((opt) => {
                  const isActive = settings.theme === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => { updateSettings({ theme: opt.value }); showSaved(); }}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer"
                      style={{
                        background: isActive ? 'var(--color-surface)' : 'transparent',
                        color: isActive ? 'var(--color-text)' : 'var(--color-text-tertiary)',
                        boxShadow: isActive ? 'var(--shadow-sm)' : 'none',
                      }}
                    >
                      <opt.icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </SettingRow>
            <SettingRow label="Font size">
              <select
                value={settings.fontSize}
                onChange={(e) => { updateSettings({ fontSize: e.target.value as any }); showSaved(); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="small">Small</option>
                <option value="default">Default</option>
                <option value="large">Large</option>
              </select>
            </SettingRow>
          </Section>

          {/* Connection */}
          <Section title="Connection">
            <SettingRow label="Server status" description={serverInfo ? `${serverInfo.engine} / ${serverInfo.model}` : 'Not connected'}>
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: healthy === true ? 'var(--color-success)' : healthy === false ? 'var(--color-error)' : 'var(--color-text-tertiary)' }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {healthy === true ? 'Connected' : healthy === false ? 'Disconnected' : 'Checking...'}
                </span>
              </div>
            </SettingRow>
            <SettingRow
              label="Diagnostics"
              description="Copy a short status report (Ollama process, reachability, version, logs, disk, memory, Nivos version) with no secrets"
            >
              <DiagnosticsButton />
            </SettingRow>
            <SettingRow label="API URL" description="Set if backend runs on a different port or host">
              <input
                type="text"
                value={settings.apiUrl}
                onChange={(e) => { updateSettings({ apiUrl: e.target.value }); showSaved(); }}
                placeholder="http://localhost:8000"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
            <SettingRow label="API key" description="Required only if the server was started with an API key">
              <input
                type="password"
                value={settings.apiKey}
                onChange={(e) => { updateSettings({ apiKey: e.target.value }); showSaved(); }}
                placeholder="NIVOS_API_KEY"
                autoComplete="off"
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              />
            </SettingRow>
          </Section>

          {/* Inference source */}
          <Section title="Inference source">
            <SettingRow label="Source" description="Where the app runs models. Applies after restart.">
              <select
                value={srcKind}
                onChange={(e) => { setSrcKind(e.target.value as InferenceSource['kind']); setSrcMsg(''); }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              >
                <option value="ollama">Bundled Ollama (default)</option>
                <option value="custom">Custom OpenAI-compatible server</option>
              </select>
            </SettingRow>
            {srcKind === 'custom' && (
              <>
                <SettingRow label="Server URL" description="e.g. LM Studio: http://localhost:1234/v1">
                  <input type="text" value={customHost} onChange={(e) => { setCustomHost(e.target.value); setSrcMsg(''); }} placeholder="http://localhost:1234/v1"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Model" description="Model id served by your endpoint">
                  <input type="text" value={customModel} onChange={(e) => { setCustomModel(e.target.value); setSrcMsg(''); }} placeholder="qwen2.5-7b-instruct"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
                <SettingRow label="Server type" description="OpenAI-compatible engine">
                  <select value={customEngine} onChange={(e) => { setCustomEngine(e.target.value); setSrcMsg(''); }}
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                    <option value="lmstudio">LM Studio</option>
                    <option value="vllm">vLLM</option>
                    <option value="sglang">SGLang</option>
                    <option value="llamacpp">llama.cpp</option>
                    <option value="mlx">MLX</option>
                  </select>
                </SettingRow>
                <SettingRow label="API key (optional)" description="Only if your server requires one">
                  <input type="password" value={customKey} onChange={(e) => { setCustomKey(e.target.value); setSrcMsg(''); }} placeholder="leave blank if none"
                    className="text-sm px-3 py-1.5 rounded-lg outline-none w-56"
                    style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }} />
                </SettingRow>
              </>
            )}
            <SettingRow label="" description={srcMsg}>
              <button onClick={saveSource}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{ background: 'var(--color-accent, var(--color-bg-tertiary))', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
                Save inference source
              </button>
            </SettingRow>
          </Section>

          {/* Models */}
          <Section title="Models">
            <SettingRow label="Local models (Ollama)" description="Models available for local inference">
              <OllamaModelList />
            </SettingRow>
            <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
              Run <code className="px-1 py-0.5 rounded text-[11px]" style={{ background: 'var(--color-bg-tertiary)' }}>ollama pull &lt;model-name&gt;</code> in your terminal to add more models
            </div>
            <SettingRow label="Cloud providers" description="Green dot means API key is configured">
              <div className="flex flex-wrap gap-3">
                <CloudProviderStatus label="OpenAI" keyName="OPENAI_API_KEY" />
                <CloudProviderStatus label="Anthropic" keyName="ANTHROPIC_API_KEY" />
                <CloudProviderStatus label="Google" keyName="GEMINI_API_KEY" />
                <CloudProviderStatus label="OpenRouter" keyName="OPENROUTER_API_KEY" />
                <CloudProviderStatus label="Atlas Cloud" keyName="ATLASCLOUD_API_KEY" />
              </div>
            </SettingRow>
          </Section>

          {/* Model Routing Tiers */}
          <ModelRoutingSection />

          {/* Location & Time */}
          <LocationContextSection />

          {/* Cloud models */}
          <Section title="Cloud models">
            <SettingRow label="OpenAI" description="GPT-4o, o3-mini, etc.">
              <ApiKeyInput keyName="OPENAI_API_KEY" placeholder="sk-..." />
            </SettingRow>
            <SettingRow label="Anthropic" description="Claude 3.5/4 models">
              <ApiKeyInput keyName="ANTHROPIC_API_KEY" placeholder="sk-ant-..." />
            </SettingRow>
            <SettingRow label="Google Gemini" description="Gemini models">
              <ApiKeyInput keyName="GEMINI_API_KEY" placeholder="AI..." />
            </SettingRow>
            <SettingRow label="OpenRouter" description="Multi-provider routing">
              <ApiKeyInput keyName="OPENROUTER_API_KEY" placeholder="sk-or-..." />
            </SettingRow>
            <SettingRow label="Atlas Cloud" description="Models routed through Atlas Cloud">
              <ApiKeyInput keyName="ATLASCLOUD_API_KEY" placeholder="Atlas Cloud API key" />
            </SettingRow>
            <SettingRow
              label="Allow Obsidian vault notes to cloud models"
              description="By default, Obsidian vault note contents are never sent to cloud models for your privacy"
            >
              <button
                type="button"
                onClick={async () => {
                  const next = !allowObsidianToCloud;
                  setAllowObsidianToCloud(next);
                  await savePrivacySettings({ allow_obsidian_to_cloud: next });
                  showSaved();
                }}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors ${
                  allowObsidianToCloud ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-border)]'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform mt-0.5 ${
                    allowObsidianToCloud ? 'translate-x-4 ml-0.5' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </SettingRow>
          </Section>

          {/* Tools */}
          <Section title="Tools">
            <SettingRow label="Web Search" description="Tavily key for web search tool">
              <ApiKeyInput keyName="TAVILY_API_KEY" placeholder="tvly-..." toolName="web_search" />
            </SettingRow>
          </Section>

          {/* MCP Servers & Plugins */}
          <McpServersSection />

          {/* Memory */}
          <Section title="Memory">
            <MemoryStatusRow status={memoryStatus} onRetry={() => { void refreshMemoryStatus(); }} />
            <SettingRow label="Use memory context" description="Automatically inject relevant memories into conversations">
              <button
                onClick={() => {
                  const next = !memoryEnabled;
                  setMemoryEnabled(next);
                  try { localStorage.setItem('nivos-memory-enabled', String(next)); } catch {}
                  showSaved();
                }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: memoryEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: memoryEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Memory backend" description="Which retrieval engine to use">
              <select
                value={memoryBackend}
                onChange={(e) => {
                  setMemoryBackend(e.target.value);
                  try { localStorage.setItem('nivos-memory-backend', e.target.value); } catch {}
                  showSaved();
                }}
                className="text-sm px-3 py-1.5 rounded-lg outline-none cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <option value="sqlite">sqlite</option>
                <option value="faiss">faiss</option>
                <option value="bm25">bm25</option>
                <option value="colbert">colbert</option>
                <option value="hybrid">hybrid</option>
              </select>
            </SettingRow>
            <SettingRow label="Results to inject" description={`${memoryTopK}`}>
              <input
                type="range"
                min="1"
                max="20"
                step="1"
                value={memoryTopK}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryTopK(v);
                  try { localStorage.setItem('nivos-memory-top-k', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Min relevance score" description={`${memoryMinScore}`}>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={memoryMinScore}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setMemoryMinScore(v);
                  try { localStorage.setItem('nivos-memory-min-score', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max context tokens" description={`${memoryMaxTokens}`}>
              <input
                type="range"
                min="256"
                max="8192"
                step="256"
                value={memoryMaxTokens}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setMemoryMaxTokens(v);
                  try { localStorage.setItem('nivos-memory-max-tokens', String(v)); } catch {}
                  showSaved();
                }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Obsidian Vault */}
          <ObsidianVaultSection />

          {/* Memory Review */}
          <MemoryReviewSection />

          {/* Applications & Launch Controls */}
          <AppsManagementSection />

          {/* Docker Sandbox */}
          <SandboxSettingsSection />


          {/* Self-Improvement Review */}
          <SelfDevReviewSection />

          {/* Model defaults */}
          <Section title="Model Defaults">
            <SettingRow label="Temperature" description={`${settings.temperature}`}>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={settings.temperature}
                onChange={(e) => { updateSettings({ temperature: parseFloat(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
            <SettingRow label="Max tokens" description={`${settings.maxTokens}`}>
              <input
                type="range"
                min="256"
                max="32768"
                step="256"
                value={settings.maxTokens}
                onChange={(e) => { updateSettings({ maxTokens: parseInt(e.target.value) }); showSaved(); }}
                className="w-32 cursor-pointer accent-[var(--color-accent)]"
              />
            </SettingRow>
          </Section>

          {/* Speech */}
          <Section title="Speech">
            <SettingRow label="Speech-to-Text" description="Enable microphone input for voice dictation">
              <button
                onClick={() => { updateSettings({ speechEnabled: !settings.speechEnabled }); showSaved(); }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.speechEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.speechEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Voice" description="Select voice model for spoken replies (Kokoro neural TTS)">
              <div className="flex flex-col gap-2 w-full max-w-sm">
                <div className="flex items-center gap-2">
                  <select
                    value={selectedVoice}
                    onChange={(e) => handleVoiceChange(e.target.value)}
                    className="flex-1 px-2.5 py-1.5 text-xs rounded border cursor-pointer"
                    style={{
                      background: 'var(--color-bg-secondary)',
                      borderColor: 'var(--color-border)',
                      color: 'var(--color-text-primary)',
                    }}
                  >
                    <option value="bm_george">George (British Male - Default)</option>
                    <option value="bm_lewis">Lewis (British Male)</option>
                    <option value="bf_emma">Emma (British Female)</option>
                    <option value="bf_isabella">Isabella (British Female)</option>
                    <option value="af_heart">Heart (American Female)</option>
                    <option value="af_bella">Bella (American Female)</option>
                    <option value="af_nicole">Nicole (American Female)</option>
                    <option value="af_sarah">Sarah (American Female)</option>
                    <option value="af_sky">Sky (American Female)</option>
                    <option value="am_adam">Adam (American Male)</option>
                    <option value="am_michael">Michael (American Male)</option>
                    <option value="zf_xiaoxiao">Xiaoxiao (Mandarin Female)</option>
                    <option value="zm_yunjian">Yunjian (Mandarin Male)</option>
                    <option value="jf_alpha">Alpha (Japanese Female)</option>
                    <option value="jm_kumo">Kumo (Japanese Male)</option>
                  </select>
                  <button
                    type="button"
                    onClick={handleTestVoice}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors cursor-pointer"
                    style={{
                      background: testingVoice ? 'var(--color-error)' : 'var(--color-accent)',
                      color: '#ffffff',
                    }}
                    title={testingVoice ? 'Stop test playback' : 'Play voice test'}
                  >
                    {testingVoice ? <Square size={12} /> : <Volume2 size={12} />}
                    {testingVoice ? 'Stop' : 'Test'}
                  </button>
                </div>
                {testVoiceError && (
                  <p className="text-xs" style={{ color: 'var(--color-error)' }}>
                    {testVoiceError}
                  </p>
                )}
              </div>
            </SettingRow>
            <SettingRow label="Read replies aloud" description="Automatically read assistant replies aloud as they finish">
              <button
                onClick={() => {
                  const next = !settings.voiceAutoplay;
                  updateSettings({ voiceAutoplay: next });
                  updateTtsConfig({ read_aloud: next }).catch(() => {});
                  showSaved();
                }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.voiceAutoplay ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.voiceAutoplay ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Speaker button on replies" description="Show a read-aloud speaker button on assistant replies">
              <button
                onClick={() => { updateSettings({ voiceOutputEnabled: !settings.voiceOutputEnabled }); showSaved(); }}
                className="relative w-11 h-6 rounded-full transition-colors cursor-pointer"
                style={{
                  background: settings.voiceOutputEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                }}
              >
                <span
                  className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-white"
                  style={{
                    transform: settings.voiceOutputEnabled ? 'translateX(20px)' : 'translateX(0)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="TTS Backend status" description="Kokoro neural speech running locally on CPU">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: ttsBackend?.available ? 'var(--color-success)' : 'var(--color-text-tertiary)',
                  }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {ttsBackend === null ? 'Checking...'
                    : ttsBackend.available ? `Kokoro TTS (${selectedVoice}) ready`
                    : (ttsBackend.reason || 'Kokoro backend not initialized')}
                </span>
              </div>
            </SettingRow>
            <SettingRow label="Backend status" description="Requires Whisper, Deepgram, or another speech backend">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: speechBackendAvailable === true ? 'var(--color-success)'
                      : speechBackendAvailable === false ? 'var(--color-text-tertiary)'
                      : 'var(--color-text-tertiary)',
                  }}
                />
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {speechBackendAvailable === null ? 'Checking...'
                    : speechBackendAvailable ? 'Available'
                    : 'Not configured'}
                </span>
              </div>
            </SettingRow>
            {!speechBackendAvailable && speechBackendAvailable !== null && (
              <div className="text-xs mt-2 px-1" style={{ color: 'var(--color-text-tertiary)' }}>
                Set up a speech backend to use voice input.
                See the <a href="https://nivos.github.io/nivos/user-guide/tools/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)' }}>documentation</a> for details.
              </div>
            )}
          </Section>

          {/* Data */}
          <Section title="Data">
            <SettingRow label="Conversations" description={`${conversations.length} stored locally`}>
              <div className="flex gap-2">
                <button
                  onClick={handleExport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Download size={12} /> Export
                </button>
                <button
                  onClick={handleImport}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                  style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                >
                  <Upload size={12} /> Import
                </button>
              </div>
            </SettingRow>
            <SettingRow label="Clear all data" description="Permanently delete all conversations">
              <button
                onClick={handleClear}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
                style={{
                  color: confirmClear ? 'white' : 'var(--color-error)',
                  background: confirmClear ? 'var(--color-error)' : 'transparent',
                  border: '1px solid var(--color-error)',
                }}
                onMouseEnter={(e) => { if (!confirmClear) e.currentTarget.style.background = 'rgba(220,38,38,0.1)'; }}
                onMouseLeave={(e) => { if (!confirmClear) e.currentTarget.style.background = 'transparent'; }}
              >
                <Trash2 size={12} /> {confirmClear ? 'Click again to confirm' : 'Clear'}
              </button>
            </SettingRow>
          </Section>

          {/* Updates */}
          <Section title="Updates">
            <SettingRow label="Auto-update" description="Check for new desktop builds automatically every 30 minutes">
              <button
                onClick={() => handleAutoUpdateToggle(!autoUpdateEnabled)}
                className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
                style={{ background: autoUpdateEnabled ? 'var(--color-accent)' : 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}
              >
                <span
                  className="inline-block h-3.5 w-3.5 rounded-full transition-transform"
                  style={{
                    background: 'white',
                    transform: autoUpdateEnabled ? 'translateX(18px)' : 'translateX(2px)',
                  }}
                />
              </button>
            </SettingRow>
            <SettingRow label="Check for updates" description="Manually check for a new version right now">
              <button
                onClick={handleCheckNow}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-text)', cursor: 'pointer' }}
                disabled={updateCheckState === 'checking'}
              >
                <RefreshCw size={12} className={updateCheckState === 'checking' ? 'animate-spin' : ''} />
                {updateCheckState === 'checking' && 'Checking...'}
                {updateCheckState === 'available' && 'Update available — see banner above'}
                {updateCheckState === 'latest' && 'Already up to date'}
                {updateCheckState === 'idle' && 'Check now'}
              </button>
            </SettingRow>
          </Section>

          {/* About */}
          <Section title="About">
            <div className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              <p className="mb-2">
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Nivos</span> — Programming abstractions for on-device AI.
              </p>
              <div className="flex gap-3 mt-3 text-xs">
                <a
                  href="https://nivos.github.io/nivos/"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--color-accent)' }}
                >
                  Documentation
                </a>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
