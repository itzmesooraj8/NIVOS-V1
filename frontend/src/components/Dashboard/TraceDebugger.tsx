import { useState, useEffect, useCallback } from 'react';
import {
  Activity as ActivityIcon,
  Clock,
  ChevronRight,
  ChevronDown,
  Globe,
  FileText,
  Sparkles,
  Terminal,
  Database,
  Play,
  Pause,
  Trash2,
  CheckCircle2,
} from 'lucide-react';
import { fetchRecordingStatus, setRecordingStatus, clearTraces as apiClearTraces } from '../../lib/api';

interface TraceStepData {
  model?: string;
  tokens?: number;
  tool?: string;
  input?: unknown;
  output?: unknown;
  [key: string]: unknown;
}

interface TraceStep {
  step_type: string;
  duration_ms?: number;
  duration_seconds?: number;
  data?: TraceStepData;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
}

interface TraceSummary {
  id: string;
  query: string;
  duration_ms?: number;
  steps: TraceStep[];
  created_at: string;
  engine?: string;
  model?: string;
  metadata?: Record<string, unknown>;
}

function getStepDurationMs(step: TraceStep): number {
  if (typeof step.duration_ms === 'number' && !isNaN(step.duration_ms)) {
    return step.duration_ms;
  }
  if (typeof step.duration_seconds === 'number' && !isNaN(step.duration_seconds)) {
    return Math.round(step.duration_seconds * 1000);
  }
  return 0;
}

function formatDuration(ms: number): string {
  if (!ms || isNaN(ms) || ms < 0) return '0ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function getStepDetails(step: TraceStep): { title: string; subtitle?: string; icon: typeof Sparkles } {
  const type = step.step_type?.toLowerCase() || '';
  const data = step.data || {};
  const dataLeft = Boolean(data.data_left_device || (step.metadata as any)?.data_left_device || (step.output as any)?.data_left_device);
  const stepEngine = (data.engine || (step.metadata as any)?.engine || (step.output as any)?.engine || '') as string;
  if (dataLeft) {
    return {
      title: `Answered by ${stepEngine || 'Cloud'}`,
      subtitle: `Data left machine (Cloud: ${data.model || (step.metadata as any)?.model || 'cloud'})`,
      icon: Globe,
    };
  }

  const tool = (
    (data.tool ||
      (step.input as any)?.tool ||
      (step.metadata as any)?.tool ||
      '') as string
  ).toLowerCase();
  const input = data.input ?? step.input;

  if (type === 'tool_call' || tool) {
    if (
      tool.includes('web_search') ||
      tool.includes('search') ||
      tool.includes('browse') ||
      tool.includes('google')
    ) {
      const q =
        typeof input === 'string'
          ? input
          : (input as any)?.query || (input as any)?.q;
      return { title: 'Searched the web', subtitle: q, icon: Globe };
    }
    if (
      tool.includes('read_file') ||
      tool.includes('fetch') ||
      tool.includes('cat')
    ) {
      const path =
        typeof input === 'object' && input
          ? (input as any).Path || (input as any).path
          : '';
      return { title: 'Read a file', subtitle: path, icon: FileText };
    }
    if (
      tool.includes('write_file') ||
      tool.includes('edit_file') ||
      tool.includes('patch')
    ) {
      const path =
        typeof input === 'object' && input
          ? (input as any).Path || (input as any).path
          : '';
      return { title: 'Edited a file', subtitle: path, icon: FileText };
    }
    if (
      tool.includes('shell') ||
      tool.includes('bash') ||
      tool.includes('command') ||
      tool.includes('terminal')
    ) {
      const cmd =
        typeof input === 'object' && input
          ? (input as any).CommandLine || (input as any).cmd
          : '';
      return { title: 'Ran command in workspace', subtitle: cmd, icon: Terminal };
    }
    if (tool.includes('memory') || tool.includes('recall')) {
      return {
        title: 'Searched memory',
        subtitle: typeof input === 'string' ? input : (input as any)?.query,
        icon: Database,
      };
    }
    return {
      title: `Used tool: ${tool}`,
      subtitle: typeof input === 'string' ? input : undefined,
      icon: Terminal,
    };
  }

  if (type === 'generate') {
    const model = (data.model || (step.metadata as any)?.model) as string;
    return {
      title: 'Asked the model',
      subtitle: model ? `Model: ${model}` : undefined,
      icon: Sparkles,
    };
  }

  if (type === 'route') {
    const model = (data.model || (step.output as any)?.model) as string;
    return {
      title: 'Selected model',
      subtitle: model ? `Model: ${model}` : undefined,
      icon: CheckCircle2,
    };
  }

  if (type === 'retrieve') {
    return {
      title: 'Retrieved context',
      subtitle: data.results ? `${data.results} sources` : undefined,
      icon: Database,
    };
  }

  if (type === 'respond') {
    return { title: 'Formulated response', icon: CheckCircle2 };
  }

  return {
    title: type ? type.charAt(0).toUpperCase() + type.slice(1) : 'Processed step',
    icon: CheckCircle2,
  };
}

function TraceCard({
  trace,
  isActive,
  onClick,
}: {
  trace: TraceSummary;
  isActive: boolean;
  onClick: () => void;
}) {
  const totalMs =
    typeof trace.duration_ms === 'number' && !isNaN(trace.duration_ms)
      ? trace.duration_ms
      : (trace.steps || []).reduce((sum, s) => sum + getStepDurationMs(s), 0);

  return (
    <button
      onClick={onClick}
      className="w-full text-left p-3 rounded-lg transition-colors cursor-pointer"
      style={{
        background: isActive ? 'var(--color-bg-tertiary)' : 'transparent',
        border: isActive ? '1px solid var(--color-border)' : '1px solid transparent',
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.background = 'var(--color-bg-secondary)';
      }}
      onMouseLeave={(e) => {
        if (!isActive) e.currentTarget.style.background = 'transparent';
      }}
    >
      <div className="text-sm truncate mb-1" style={{ color: 'var(--color-text)' }}>
        {trace.query || 'Untitled interaction'}
      </div>
      {(trace.engine?.toLowerCase().includes('cloud') || trace.steps?.some(s => s.data?.data_left_device || (s.metadata as any)?.data_left_device)) && (
        <div className="mb-1">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-sky-500/10 text-sky-400 font-medium border border-sky-500/20">
            <Globe size={10} />
            Data left machine ({trace.engine || 'Cloud'})
          </span>
        </div>
      )}
      <div
        className="flex items-center gap-2 text-[11px]"
        style={{ color: 'var(--color-text-tertiary)' }}
      >
        <span>{trace.steps?.length || 0} steps</span>
        <span>&middot;</span>
        <span>{formatDuration(totalMs)}</span>
        <span>&middot;</span>
        <span>
          {trace.created_at ? new Date(trace.created_at).toLocaleTimeString() : 'Recent'}
        </span>
      </div>
    </button>
  );
}

function StepDetail({ step, index }: { step: TraceStep; index: number }) {
  const [showDetails, setShowDetails] = useState(false);
  const durMs = getStepDurationMs(step);
  const { title, subtitle, icon: StepIcon } = getStepDetails(step);

  const rawData: Record<string, unknown> = {
    step_type: step.step_type,
    duration_ms: durMs,
    ...(step.data || {}),
    input: step.data?.input ?? step.input,
    output: step.data?.output ?? step.output,
    metadata: step.metadata,
  };

  const filteredEntries = Object.entries(rawData).filter(
    ([_, v]) => v != null && v !== '' && (typeof v !== 'object' || Object.keys(v).length > 0)
  );

  return (
    <div
      className="rounded-lg overflow-hidden transition-all"
      style={{
        border: '1px solid var(--color-border)',
        background: 'var(--color-surface)',
      }}
    >
      {/* Plain language summary bar */}
      <div className="flex items-center gap-3 px-3 py-2.5">
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
          style={{
            background: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
            color: 'var(--color-accent)',
          }}
        >
          <StepIcon size={14} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
            {title}
          </div>
          {subtitle && (
            <div
              className="text-xs truncate font-mono mt-0.5"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              {subtitle}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <span
            className="text-xs font-mono flex items-center gap-1 px-2 py-0.5 rounded"
            style={{
              background: 'var(--color-bg-secondary)',
              color: 'var(--color-text-tertiary)',
            }}
          >
            <Clock size={11} />
            {formatDuration(durMs)}
          </span>

          <button
            onClick={() => setShowDetails(!showDetails)}
            className="flex items-center gap-1 text-[11px] px-2 py-1 rounded transition-colors cursor-pointer"
            style={{
              background: showDetails
                ? 'var(--color-bg-tertiary)'
                : 'var(--color-bg-secondary)',
              color: 'var(--color-text-secondary)',
              border: '1px solid var(--color-border)',
            }}
            title="Toggle raw technical detail"
          >
            {showDetails ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <span>Details</span>
          </button>
        </div>
      </div>

      {/* Raw technical details behind expander */}
      {showDetails && (
        <div
          className="px-4 py-3 text-xs font-mono space-y-1.5"
          style={{
            borderTop: '1px solid var(--color-border)',
            background: 'var(--color-bg-secondary)',
          }}
        >
          <div
            className="text-[10px] uppercase tracking-wider font-semibold mb-2"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            Raw Execution Details & Data
          </div>
          {filteredEntries.map(([key, val]) => (
            <div key={key} className="flex gap-2">
              <span
                className="shrink-0 font-semibold"
                style={{ color: 'var(--color-text-tertiary)', minWidth: '90px' }}
              >
                {key}:
              </span>
              <span
                className="break-all whitespace-pre-wrap"
                style={{ color: 'var(--color-text)' }}
              >
                {typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Activity() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(true);

  // Auto-delete entries older than 30 days and fetch
  const fetchTraces = useCallback(async () => {
    try {
      const base = import.meta.env.VITE_API_URL || '';
      const res = await fetch(`${base}/v1/traces?limit=50`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      const rawList: TraceSummary[] = data.traces || [];

      // Client-side auto-delete entries older than 30 days
      const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;
      const recent = rawList.filter((t) => {
        if (!t.created_at) return true;
        const time = new Date(t.created_at).getTime();
        return isNaN(time) || time >= thirtyDaysAgo;
      });

      setTraces(recent);
      setError(null);
    } catch {
      setError('Cannot load activity history');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTraces();
    fetchRecordingStatus().then(setPaused).catch(() => {});
  }, [fetchTraces]);

  const handleTogglePause = async () => {
    const next = !paused;
    setPaused(next);
    await setRecordingStatus(next);
  };

  const handleClearHistory = async () => {
    if (confirm('Clear all activity history? This cannot be undone.')) {
      try {
        await apiClearTraces();
        setTraces([]);
        setSelectedId(null);
      } catch (err) {
        console.error('Failed to clear activity:', err);
      }
    }
  };

  const selected = traces.find((t) => t.id === selectedId);

  return (
    <div className="hud-panel p-6">
      {/* Header with Title, Pause Switch, and Clear History */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4 pb-3" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <div className="flex items-center gap-2">
          <ActivityIcon size={16} style={{ color: 'var(--color-accent)' }} />
          <div>
            <h3 className="hud-label text-sm font-semibold m-0" style={{ color: 'var(--color-text)' }}>
              Activity
            </h3>
            <p className="text-xs m-0" style={{ color: 'var(--color-text-tertiary)' }}>
              Real-time plain-language log of assistant steps and tool actions
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Pause recording toggle */}
          <button
            onClick={handleTogglePause}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
            style={{
              background: paused
                ? 'color-mix(in srgb, var(--color-warning) 15%, transparent)'
                : 'var(--color-bg-secondary)',
              color: paused ? 'var(--color-warning)' : 'var(--color-text-secondary)',
              border: `1px solid ${paused ? 'var(--color-warning)' : 'var(--color-border)'}`,
            }}
            title={paused ? 'Resume recording activity' : 'Pause recording activity'}
          >
            {paused ? <Play size={12} /> : <Pause size={12} />}
            <span>{paused ? 'Recording Paused' : 'Recording Active'}</span>
          </button>

          {/* Clear history button */}
          <button
            onClick={handleClearHistory}
            disabled={traces.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            style={{
              background: 'var(--color-bg-secondary)',
              color: 'var(--color-text-secondary)',
              border: '1px solid var(--color-border)',
            }}
            title="Clear all activity history"
          >
            <Trash2 size={12} />
            <span>Clear history</span>
          </button>
        </div>
      </div>

      {error ? (
        <div className="h-48 flex items-center justify-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
          <span className="hud-mono">{error}</span>
        </div>
      ) : traces.length === 0 ? (
        <div className="h-48 flex flex-col items-center justify-center gap-2 text-sm text-center" style={{ color: 'var(--color-text-tertiary)' }}>
          <ActivityIcon size={24} className="opacity-40" />
          <span>{loading ? 'Loading activity...' : 'No activity yet. Start chatting to see your assistant’s actions here.'}</span>
        </div>
      ) : (
        <div className="flex gap-4 h-96">
          {/* Left: Activity list */}
          <div
            className="w-1/3 overflow-y-auto flex flex-col gap-1 pr-2"
            style={{ borderRight: '1px solid var(--color-border)' }}
          >
            {traces.map((trace) => (
              <TraceCard
                key={trace.id}
                trace={trace}
                isActive={trace.id === selectedId}
                onClick={() => setSelectedId(trace.id)}
              />
            ))}
          </div>

          {/* Right: Selected activity steps */}
          <div className="flex-1 overflow-y-auto pl-1">
            {selected ? (
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between pb-2 mb-1" style={{ borderBottom: '1px solid var(--color-border)' }}>
                  <div className="text-sm font-semibold truncate" style={{ color: 'var(--color-text)' }}>
                    {selected.query || 'Untitled interaction'}
                  </div>
                  <span className="text-xs shrink-0" style={{ color: 'var(--color-text-tertiary)' }}>
                    {selected.steps?.length || 0} steps
                  </span>
                </div>

                {selected.steps && selected.steps.length > 0 ? (
                  selected.steps.map((step, i) => (
                    <StepDetail key={i} step={step} index={i} />
                  ))
                ) : (
                  <div className="py-6 text-center text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                    No steps recorded for this interaction.
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
                Select an interaction on the left to see its steps.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Export as both Activity and TraceDebugger for compatibility
export { Activity as TraceDebugger };
