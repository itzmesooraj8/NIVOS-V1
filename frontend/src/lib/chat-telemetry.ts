const CLOUD_PREFIXES = [
  'gpt-',
  'o1-',
  'o3-',
  'o4-',
  'claude-',
  'gemini-',
  'openrouter/',
  'MiniMax-',
  'chatgpt-',
];

export function engineFromCompletionChunk(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const telemetry = (data as { telemetry?: unknown }).telemetry;
  if (!telemetry || typeof telemetry !== 'object') return undefined;
  const engine = (telemetry as { engine?: unknown }).engine;
  if (typeof engine !== 'string' || !engine.trim()) return undefined;
  return engine.trim();
}

export function resolveChatEngine({
  routedEngine,
  serverEngine,
  selectedModel,
  selectedOwner,
}: {
  routedEngine?: string;
  serverEngine?: string;
  selectedModel: string;
  selectedOwner?: string;
}): string {
  // The finish chunk describes the backend that handled this exact request.
  // /v1/info describes only the server's configured wrapper (often "multi").
  if (routedEngine?.trim()) return routedEngine.trim();
  if (selectedOwner === 'litellm') return 'litellm';
  if (selectedOwner === 'ollama') return 'ollama';
  if (CLOUD_PREFIXES.some((prefix) => selectedModel.startsWith(prefix))) {
    return 'cloud';
  }
  // Ollama models always go to Ollama (colon tags like qwen3.5:9b, or family names)
  if (
    selectedModel.includes(':') ||
    selectedModel.startsWith('qwen') ||
    selectedModel.startsWith('gemma') ||
    selectedModel.startsWith('llama')
  ) {
    return 'ollama';
  }
  if (serverEngine?.trim() && serverEngine !== 'multi') {
    return serverEngine.trim();
  }
  return 'ollama';
}

export function getCloudProvider(model: string): string | null {
  if (!model) return null;
  if (model === 'gpt-oss' || model.startsWith('gpt-oss:') || model.startsWith('gpt-oss-')) return null;
  if (
    model.startsWith('gpt-') ||
    model.startsWith('o1-') ||
    model.startsWith('o3-') ||
    model.startsWith('o4-') ||
    model.startsWith('chatgpt-')
  ) {
    return 'OpenAI';
  }
  if (model.startsWith('claude-')) return 'Anthropic';
  if (model.startsWith('gemini-')) return 'Google Gemini';
  if (model.startsWith('atlascloud/')) return 'Atlas Cloud';
  if (model.startsWith('MiniMax-')) return 'MiniMax';
  if (model.includes('/') || model.startsWith('openrouter/')) return 'OpenRouter';
  return null;
}
