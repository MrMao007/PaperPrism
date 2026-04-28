/**
 * Built-in LLM provider presets used by the Options first-run wizard.
 *
 * Each preset resolves into the four fields the Agent's llm.yaml expects:
 * - provider:    "openai" | "ollama" | ...   (how llm.py dispatches)
 * - api_base:    null or URL                  (empty for OpenAI default)
 * - api_key_env: allowlisted env name         (empty for local ollama)
 * - model:       sensible default; user can edit later
 *
 * Presets only cover `api_key_env` names already in the Agent allowlist
 * (see launchd.py::_SECRET_ALLOWLIST); adding a preset here without
 * extending the allowlist will fail on save.
 */

export interface ProviderPreset {
  id: string;
  label: string;
  description: string;
  provider: string;
  api_base: string;
  api_key_env: string;
  model: string;
  /** When false, the wizard skips the API key prompt (e.g. local Ollama). */
  needsKey: boolean;
  /** Displayed to the user where to obtain a key (Step 3). */
  keyHelpUrl?: string;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: 'dashscope',
    label: '阿里云百炼 (Qwen)',
    description: 'Qwen 系列模型，OpenAI 兼容端点，国内访问稳定',
    provider: 'openai',
    api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    api_key_env: 'DASHSCOPE_API_KEY',
    model: 'qwen3-max',
    needsKey: true,
    keyHelpUrl: 'https://bailian.console.aliyun.com/?apiKey=1',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    description: 'gpt-4o-mini / gpt-4o 等官方模型',
    provider: 'openai',
    api_base: '',
    api_key_env: 'OPENAI_API_KEY',
    model: 'gpt-4o-mini',
    needsKey: true,
    keyHelpUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    description: '性价比高的国产模型，OpenAI 兼容',
    provider: 'openai',
    api_base: 'https://api.deepseek.com/v1',
    api_key_env: 'DEEPSEEK_API_KEY',
    model: 'deepseek-chat',
    needsKey: true,
    keyHelpUrl: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'moonshot',
    label: 'Moonshot (Kimi)',
    description: '月之暗面 Kimi 系列',
    provider: 'openai',
    api_base: 'https://api.moonshot.cn/v1',
    api_key_env: 'MOONSHOT_API_KEY',
    model: 'moonshot-v1-8k',
    needsKey: true,
    keyHelpUrl: 'https://platform.moonshot.cn/console/api-keys',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    description: '聚合网关，一键切换海量模型',
    provider: 'openai',
    api_base: 'https://openrouter.ai/api/v1',
    api_key_env: 'OPENROUTER_API_KEY',
    model: 'openai/gpt-4o-mini',
    needsKey: true,
    keyHelpUrl: 'https://openrouter.ai/keys',
  },
  {
    id: 'anthropic',
    label: 'Anthropic (Claude)',
    description: 'Claude 3.5 Sonnet 等，原生 Anthropic Messages API',
    provider: 'anthropic',
    api_base: '',
    api_key_env: 'ANTHROPIC_API_KEY',
    model: 'claude-3-5-sonnet-latest',
    needsKey: true,
    keyHelpUrl: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'gemini',
    label: 'Google Gemini',
    description: 'Gemini 2.0 / 1.5，走官方 OpenAI 兼容端点',
    provider: 'openai',
    api_base: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    api_key_env: 'GEMINI_API_KEY',
    model: 'gemini-2.0-flash',
    needsKey: true,
    keyHelpUrl: 'https://aistudio.google.com/app/apikey',
  },
  {
    id: 'ollama',
    label: 'Ollama 本地',
    description: '本机跑开源模型，完全免费、免 API key',
    provider: 'ollama',
    api_base: 'http://localhost:11434/v1',
    api_key_env: '',
    model: 'qwen2.5:7b-instruct',
    needsKey: false,
  },
];

export function findPreset(id: string): ProviderPreset | undefined {
  return PROVIDER_PRESETS.find((p) => p.id === id);
}

/**
 * Best-effort detection of a running local Ollama at the default port.
 * Used by the wizard to auto-recommend the local preset when available.
 */
export async function detectOllama(timeoutMs = 800): Promise<boolean> {
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    const res = await fetch('http://localhost:11434/api/tags', {
      signal: ac.signal,
    });
    clearTimeout(t);
    return res.ok;
  } catch {
    return false;
  }
}
