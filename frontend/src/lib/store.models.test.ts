import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ModelInfo } from '../types';

class MemoryStorage {
  private store = new Map<string, string>();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

const model = (id: string): ModelInfo => ({
  id,
  object: 'model',
  created: 0,
  owned_by: 'nivos',
});

beforeEach(() => {
  vi.resetModules();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

describe('setModels', () => {
  it('does not select an embedding-only model', async () => {
    const { useAppStore } = await import('./store');

    useAppStore.getState().setModels([model('nomic-embed-text')]);

    expect(useAppStore.getState().selectedModel).toBe('');
  });

  it('clears a missing selection when no chat fallback exists', async () => {
    const { useAppStore } = await import('./store');
    useAppStore.getState().setSelectedModel('deleted-chat-model');

    useAppStore.getState().setModels([model('nomic-embed-text')]);

    expect(useAppStore.getState().selectedModel).toBe('');
  });

  it('replaces an embedding selection with an available chat model', async () => {
    const { useAppStore } = await import('./store');
    useAppStore.getState().setSelectedModel('all-minilm:latest');

    useAppStore.getState().setModels([
      model('all-minilm:latest'),
      model('qwen3.5:4b'),
    ]);

    expect(useAppStore.getState().selectedModel).toBe('qwen3.5:4b');
  });

  it('preserves auto selection and does not overwrite with qwen3.5:9b', async () => {
    const { useAppStore } = await import('./store');
    useAppStore.getState().setSelectedModel('auto');

    useAppStore.getState().setModels([
      model('qwen3.5:9b'),
      model('qwen3.5:4b'),
      model('qwen3.5:2b'),
    ]);

    expect(useAppStore.getState().selectedModel).toBe('auto');
  });

  it('restores auto selection from localStorage across reloads', async () => {
    (globalThis as unknown as { localStorage: MemoryStorage }).localStorage.setItem(
      'nivos-selected-model',
      'auto',
    );
    const { useAppStore } = await import('./store');

    expect(useAppStore.getState().selectedModel).toBe('auto');

    useAppStore.getState().setModels([
      model('qwen3.5:9b'),
      model('qwen3.5:4b'),
    ]);

    expect(useAppStore.getState().selectedModel).toBe('auto');
  });
});
