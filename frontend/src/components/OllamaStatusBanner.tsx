import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { fetchOllamaStatus, type OllamaSupervisorStatus } from '../lib/api';

export function OllamaStatusBanner() {
  const [status, setStatus] = useState<OllamaSupervisorStatus | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      const next = await fetchOllamaStatus();
      if (!active || !next) return;
      setStatus(next);
      window.dispatchEvent(new CustomEvent('nivos:ollama-status', { detail: next }));
    };
    void poll();
    const interval = setInterval(() => {
      void poll();
    }, 10_000);
    const onFocus = () => {
      void poll();
    };
    window.addEventListener('focus', onFocus);
    return () => {
      active = false;
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  if (!status?.banner) return null;

  return (
    <div
      role="status"
      data-testid="ollama-supervisor-banner"
      className="flex items-center justify-center gap-2 px-4 py-2 text-xs font-medium shrink-0"
      style={{
        background: 'rgba(245, 158, 11, 0.14)',
        color: '#b45309',
        borderBottom: '1px solid rgba(245, 158, 11, 0.35)',
      }}
    >
      <AlertTriangle size={14} />
      <span>{status.banner}</span>
    </div>
  );
}

