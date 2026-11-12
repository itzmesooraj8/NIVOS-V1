import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../lib/store', () => ({ useAppStore: vi.fn() }));

import { DiagnosticsButton } from '../pages/SettingsPage';
import { OllamaStatusBanner } from './OllamaStatusBanner';

describe('Ollama supervisor UI controls', () => {
  it('renders the Diagnostics button cleanly in Settings', () => {
    const html = renderToStaticMarkup(<DiagnosticsButton />);
    expect(html).toContain('Diagnostics');
  });

  it('renders no banner initially when supervisor has not reported an outage', () => {
    const html = renderToStaticMarkup(<OllamaStatusBanner />);
    expect(html).toBe('');
  });
});

