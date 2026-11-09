import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  GitBranch,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  Play,
  Trash2,
  Copy,
  Check,
  FileCode,
  Terminal,
  RefreshCw,
  BookOpen,
} from 'lucide-react';
import {
  fetchSelfDevStatus,
  fetchSelfDevReviews,
  verifySelfDev,
  discardSelfDev,
  type SelfDevStatus,
  type SelfDevReview,
} from '../lib/api';

export function SelfDevReviewSection() {
  const [status, setStatus] = useState<SelfDevStatus | null>(null);
  const [reviews, setReviews] = useState<SelfDevReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [runningTests, setRunningTests] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const data = await fetchSelfDevReviews();
      setStatus(data.current);
      setReviews(data.history || []);
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleRunVerify = async () => {
    if (!status?.branch) return;
    setRunningTests(true);
    try {
      const topic = status.branch.replace(/^self\//, '');
      const res = await verifySelfDev({ topic });
      toast.success(res.success ? 'All verification tests passed!' : 'Tests failed; inspect output below.');
      await loadData();
    } catch (err: any) {
      toast.error(err.message || 'Verification execution failed');
    } finally {
      setRunningTests(false);
    }
  };

  const handleDiscard = async () => {
    if (!confirm('Discard all self-improvement changes and remove worktree C:\\Nivos\\self-dev?')) return;
    setLoading(true);
    try {
      await discardSelfDev();
      toast.success('Worktree discarded and pruned.');
      await loadData();
    } catch (err: any) {
      toast.error(err.message || 'Discard failed');
    } finally {
      setLoading(false);
    }
  };

  const handleCopyMerge = () => {
    if (!status?.merge_command) return;
    navigator.clipboard.writeText(status.merge_command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      {/* Overview Banner */}
      <div
        className="p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-3"
        style={{
          background: 'var(--color-bg-secondary)',
          borderColor: 'var(--color-border)',
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
            style={{
              background: status?.active ? 'rgba(6, 182, 212, 0.15)' : 'var(--color-bg-tertiary)',
              color: status?.active ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
          >
            <GitBranch size={20} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm" style={{ color: 'var(--color-text-primary)' }}>
                {status?.active ? `Active Worktree: ${status.branch}` : 'Self-Improvement Agent (Idle)'}
              </span>
              <span
                className="px-2 py-0.5 rounded-full text-[11px] font-medium"
                style={{
                  background: status?.active ? 'rgba(34, 197, 94, 0.15)' : 'var(--color-bg-tertiary)',
                  color: status?.active ? '#22c55e' : 'var(--color-text-tertiary)',
                }}
              >
                {status?.active ? 'Ready for Review' : 'Idle'}
              </span>
            </div>
            <p className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
              Worktree directory: <code className="font-mono text-[11px]">C:\Nivos\self-dev</code> (never touches master)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-end sm:self-auto">
          <button
            type="button"
            onClick={loadData}
            className="p-1.5 rounded transition-colors cursor-pointer"
            style={{ color: 'var(--color-text-secondary)' }}
            title="Refresh status"
          >
            <RefreshCw size={15} />
          </button>
          {status?.active && (
            <>
              <button
                type="button"
                onClick={handleRunVerify}
                disabled={runningTests}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors cursor-pointer"
                style={{
                  background: 'var(--color-accent)',
                  color: '#ffffff',
                }}
              >
                <Play size={12} />
                {runningTests ? 'Running Pytest...' : 'Run Tests'}
              </button>
              <button
                type="button"
                onClick={handleDiscard}
                disabled={loading}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors cursor-pointer border"
                style={{
                  borderColor: 'var(--color-border)',
                  color: 'var(--color-error, #ef4444)',
                }}
              >
                <Trash2 size={12} />
                Discard
              </button>
            </>
          )}
        </div>
      </div>

      {/* Invariants badge */}
      <div
        className="p-3 rounded-lg border text-xs flex items-start gap-2.5"
        style={{
          background: 'rgba(6, 182, 212, 0.05)',
          borderColor: 'rgba(6, 182, 212, 0.2)',
          color: 'var(--color-text-secondary)',
        }}
      >
        <ShieldCheck size={16} className="shrink-0 mt-0.5 text-cyan-500" />
        <div>
          <span className="font-semibold text-cyan-400">Strict Safety Invariant Enforced: </span>
          The agent can never modify <code className="text-cyan-300">SAFETY_RULES.md</code>, origin/policy code,
          approval checks, docker/shell tools, or <code className="text-cyan-300">me.md</code>. Pre-commit hooks reject
          unauthorized edits.
        </div>
      </div>

      {/* Active Changes & Diff */}
      {status?.active && (
        <div
          className="p-4 rounded-xl border space-y-3"
          style={{
            background: 'var(--color-bg-secondary)',
            borderColor: 'var(--color-border)',
          }}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold text-xs flex items-center gap-1.5" style={{ color: 'var(--color-text-primary)' }}>
              <FileCode size={14} />
              Review Diff & Changes
            </span>
            <button
              type="button"
              onClick={() => setShowDiff(!showDiff)}
              className="text-xs transition-colors cursor-pointer"
              style={{ color: 'var(--color-accent)' }}
            >
              {showDiff ? 'Hide Diff' : 'View Diff'}
            </button>
          </div>

          {showDiff && (
            <pre
              className="p-3 rounded text-[11px] font-mono overflow-x-auto max-h-72 border leading-relaxed"
              style={{
                background: 'var(--color-bg-primary)',
                borderColor: 'var(--color-border)',
                color: 'var(--color-text-primary)',
              }}
            >
              {status.diff_summary || 'No uncommitted changes in worktree.'}
            </pre>
          )}

          {/* Operator Merge Command Box */}
          <div
            className="p-3 rounded-lg border"
            style={{
              background: 'var(--color-bg-tertiary)',
              borderColor: 'var(--color-border)',
            }}
          >
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-medium flex items-center gap-1.5" style={{ color: 'var(--color-text-primary)' }}>
                <Terminal size={13} />
                Operator Merge Command (Manual Only)
              </span>
              <button
                type="button"
                onClick={handleCopyMerge}
                className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded transition-colors cursor-pointer"
                style={{
                  background: 'var(--color-bg-secondary)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                {copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <code className="text-xs font-mono select-all block text-cyan-400">
              {status.merge_command || `git merge ${status.branch}`}
            </code>
            <p className="text-[11px] mt-1.5" style={{ color: 'var(--color-text-tertiary)' }}>
              Nivos NEVER merges automatically. Inspect the test results above, then run this command in git to integrate.
            </p>
          </div>
        </div>
      )}

      {/* Review History */}
      {reviews.length > 0 && (
        <div
          className="p-4 rounded-xl border space-y-3"
          style={{
            background: 'var(--color-bg-secondary)',
            borderColor: 'var(--color-border)',
          }}
        >
          <span className="font-semibold text-xs flex items-center gap-1.5" style={{ color: 'var(--color-text-primary)' }}>
            <BookOpen size={14} />
            Previous Review Records
          </span>
          <div className="space-y-2">
            {reviews.map((rev, idx) => (
              <div
                key={idx}
                className="p-2.5 rounded border text-xs flex items-center justify-between"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  borderColor: 'var(--color-border)',
                }}
              >
                <div>
                  <span className="font-medium" style={{ color: 'var(--color-text-primary)' }}>
                    {rev.topic} ({rev.branch})
                  </span>
                  <p className="text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                    {rev.timestamp ? new Date(rev.timestamp).toLocaleString() : 'Past review'}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {rev.test_results?.success ? (
                    <span className="flex items-center gap-1 text-green-500 text-[11px]">
                      <CheckCircle2 size={13} /> Tests Passed
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-amber-500 text-[11px]">
                      <XCircle size={13} /> Tests Failed
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
