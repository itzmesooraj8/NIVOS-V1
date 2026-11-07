import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/api', () => ({
  fetchRecordingStatus: vi.fn(async () => false),
  setRecordingStatus: vi.fn(async () => false),
  clearTraces: vi.fn(async () => {}),
}));

import { Activity } from './TraceDebugger';

describe('Activity component', () => {
  it('renders Activity title, recording switch, and clear history button', () => {
    const html = renderToStaticMarkup(<Activity />);
    expect(html).toContain('Activity');
    expect(html).toContain('Recording Active');
    expect(html).toContain('Clear history');
    expect(html).not.toContain('Trace Debugger');
  });
});
