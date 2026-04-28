import { useEffect, useState } from 'react';
import {
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  type Settings,
} from '@/lib/settings';
import {
  fetchLlmConfig,
  saveLlmConfig,
  testLlmConfig,
  type LlmConfig,
  type LlmConfigUpdate,
} from '@/lib/agent';
import Wizard from './Wizard';

const EMPTY_LLM: LlmConfigForm = {
  provider: 'openai',
  model: '',
  api_base: '',
  api_key_env: '',
  api_key: '',
  api_key_has_value: false,
  temperature: 0,
  max_output_tokens: 600,
  timeout_seconds: 60,
  max_retries: 2,
  abstract_char_limit: 2000,
  pdf_head_char_limit: 1500,
  allowed_api_key_envs: [] as string[],
};

interface LlmConfigForm {
  provider: string;
  model: string;
  api_base: string;
  api_key_env: string;
  api_key: string; // plain, only sent when non-empty
  api_key_has_value: boolean;
  temperature: number;
  max_output_tokens: number;
  timeout_seconds: number;
  max_retries: number;
  abstract_char_limit: number;
  pdf_head_char_limit: number;
  allowed_api_key_envs: string[];
}

export default function App() {
  const [s, setS] = useState<Settings>(DEFAULT_SETTINGS);
  const [status, setStatus] = useState<string>('');
  const [agent, setAgent] = useState<'unknown' | 'online' | 'offline'>('unknown');
  const [llm, setLlm] = useState<LlmConfigForm>(EMPTY_LLM);
  const [llmStatus, setLlmStatus] = useState<string>('');
  const [llmBusy, setLlmBusy] = useState<'' | 'loading' | 'saving' | 'testing'>('');
  const [showWizard, setShowWizard] = useState<boolean>(false);
  const [settingsLoaded, setSettingsLoaded] = useState<boolean>(false);

  useEffect(() => {
    void (async () => {
      const loaded = await loadSettings();
      setS(loaded);
      setSettingsLoaded(true);
      if (!loaded.wizardCompleted) setShowWizard(true);
    })();
  }, []);

  async function onWizardComplete() {
    const merged = await saveSettings({ wizardCompleted: true });
    setS(merged);
    setShowWizard(false);
    // Refresh LLM card so the detailed form reflects what the wizard just wrote.
    void loadLlm();
  }

  useEffect(() => {
    // Best-effort initial load; silent if agent is offline.
    void loadLlm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function patch<K extends keyof Settings>(key: K, value: Settings[K]) {
    setS((cur) => ({ ...cur, [key]: value }));
  }

  async function onSave() {
    await saveSettings(s);
    setStatus('Saved.');
    setTimeout(() => setStatus(''), 1500);
  }

  async function onTestAgent() {
    await saveSettings(s);
    const res = await chrome.runtime.sendMessage({ type: 'ping-agent' });
    setAgent(res?.ok ? 'online' : 'offline');
  }

  function patchLlm<K extends keyof LlmConfigForm>(key: K, value: LlmConfigForm[K]) {
    setLlm((cur) => ({ ...cur, [key]: value }));
  }

  async function loadLlm() {
    setLlmBusy('loading');
    setLlmStatus('');
    try {
      const cfg = await fetchLlmConfig();
      setLlm({
        provider: cfg.provider,
        model: cfg.model,
        api_base: cfg.api_base,
        api_key_env: cfg.api_key_env,
        api_key: '',
        api_key_has_value: cfg.api_key_has_value,
        temperature: cfg.temperature,
        max_output_tokens: cfg.max_output_tokens,
        timeout_seconds: cfg.timeout_seconds,
        max_retries: cfg.max_retries,
        abstract_char_limit: cfg.abstract_char_limit,
        pdf_head_char_limit: cfg.pdf_head_char_limit,
        allowed_api_key_envs: cfg.allowed_api_key_envs,
      });
      setLlmStatus(`Loaded from ${cfg.path}`);
    } catch (err) {
      setLlmStatus(`Load failed: ${(err as Error).message}`);
    } finally {
      setLlmBusy('');
    }
  }

  async function onSaveLlm() {
    setLlmBusy('saving');
    setLlmStatus('');
    try {
      await saveSettings(s); // ensure agentBaseUrl/token in use are fresh
      const payload: LlmConfigUpdate = {
        provider: llm.provider,
        model: llm.model.trim(),
        api_base: llm.api_base.trim() || null,
        api_key_env: llm.api_key_env.trim() || null,
        temperature: llm.temperature,
        max_output_tokens: llm.max_output_tokens,
        timeout_seconds: llm.timeout_seconds,
        max_retries: llm.max_retries,
        abstract_char_limit: llm.abstract_char_limit,
        pdf_head_char_limit: llm.pdf_head_char_limit,
      };
      if (llm.api_key.trim()) payload.api_key = llm.api_key;
      const res = await saveLlmConfig(payload);
      setLlmStatus(
        res.secret_written
          ? `Saved. API key stored in secrets.env.`
          : `Saved to ${res.path}.`,
      );
      // Clear plain key from form after a successful save.
      setLlm((cur) => ({
        ...cur,
        api_key: '',
        api_key_has_value: res.secret_written || cur.api_key_has_value,
      }));
    } catch (err) {
      setLlmStatus(`Save failed: ${(err as Error).message}`);
    } finally {
      setLlmBusy('');
    }
  }

  async function onTestLlm() {
    setLlmBusy('testing');
    setLlmStatus('Testing... (a small chat call is being made)');
    try {
      const res = await testLlmConfig();
      if (res.ok) {
        setLlmStatus(`OK: ${res.provider_label} responded with ${res.sample}`);
      } else {
        setLlmStatus(`Failed: ${res.error}`);
      }
    } catch (err) {
      setLlmStatus(`Test endpoint error: ${(err as Error).message}`);
    } finally {
      setLlmBusy('');
    }
  }

  if (!settingsLoaded) {
    return <div className="opt-wrap"><p className="opt-sub">Loading...</p></div>;
  }

  if (showWizard) {
    return (
      <div className="opt-wrap">
        <h1 className="opt-title">PaperPrism</h1>
        <Wizard onComplete={onWizardComplete} />
      </div>
    );
  }

  return (
    <div className="opt-wrap">
      <h1 className="opt-title">PaperPrism</h1>
      <p className="opt-sub">
        Local arxiv paper organizer. Papers are saved to your normal Downloads
        folder; the local Agent mirrors each finished download into a hidden
        workspace where it runs metadata extraction and LLM classification.
      </p>

      <div className="opt-row" style={{ marginBottom: 12 }}>
        <button className="opt-btn" onClick={() => setShowWizard(true)}>
          Re-run setup wizard
        </button>
      </div>

      <details className="opt-card opt-details">
        <summary className="opt-summary">Agent connection (advanced)</summary>
        <div className="opt-field">
          <label className="opt-label">Hidden workspace path</label>
          <input
            className="opt-input"
            value={s.vaultPathHint}
            onChange={(e) => patch('vaultPathHint', e.target.value)}
            placeholder="~/.paperprism/vault"
          />
          <span className="opt-hint">
            This is a hint sent to the Agent. Paths starting with a dot are
            hidden on macOS and Linux. The extension never writes here -- the
            Agent does, after each successful download.
          </span>
        </div>

        <div className="opt-field">
          <label className="opt-label">Local Agent base URL</label>
          <input
            className="opt-input"
            value={s.agentBaseUrl}
            onChange={(e) => patch('agentBaseUrl', e.target.value)}
            placeholder="http://127.0.0.1:17321"
          />
          <span className="opt-hint">Used for /api/health and /api/ingest.</span>
        </div>

        <div className="opt-field">
          <label className="opt-label">Agent auth token (optional)</label>
          <input
            className="opt-input"
            value={s.agentToken}
            onChange={(e) => patch('agentToken', e.target.value)}
            placeholder="leave empty for local-only setups"
          />
        </div>

        <div className="opt-field">
          <label className="opt-row">
            <input
              type="checkbox"
              checked={s.archiveEnabled}
              onChange={(e) => patch('archiveEnabled', e.target.checked)}
            />
            Notify Agent to mirror finished arxiv downloads into the vault
          </label>
        </div>

        <div className="opt-field">
          <label className="opt-row">
            <input
              type="checkbox"
              checked={s.notifyEnabled}
              onChange={(e) => patch('notifyEnabled', e.target.checked)}
            />
            Show a desktop notification after each archive event
          </label>
        </div>

        <div className="opt-row">
          <button className="opt-btn primary" onClick={onSave}>Save</button>
          <button className="opt-btn" onClick={onTestAgent}>Test Agent connection</button>
          {status && <span className="opt-status-ok">{status}</span>}
          {agent === 'online' && <span className="opt-status-ok">Agent reachable.</span>}
          {agent === 'offline' && <span className="opt-status-err">Agent unreachable.</span>}
        </div>
      </details>

      {/* ---------------- LLM config ---------------- */}
      <details className="opt-card opt-details">
        <summary className="opt-summary">LLM configuration (advanced)</summary>
        <p className="opt-sub">
          These settings write to <code>~/.paperprism/llm.yaml</code> on the
          Agent host. Providing an API key also upserts it into
          <code> ~/.paperprism/secrets.env</code> (mode 600) and injects it into
          the running Agent process for immediate use.
        </p>

        <div className="opt-field">
          <label className="opt-label">Provider</label>
          <select
            className="opt-input"
            value={llm.provider}
            onChange={(e) => patchLlm('provider', e.target.value)}
          >
            <option value="openai">openai (any OpenAI-compatible endpoint)</option>
            <option value="ollama">ollama</option>
            <option value="azure">azure</option>
            <option value="custom">custom</option>
          </select>
          <span className="opt-hint">
            All providers speak OpenAI Chat Completions; set api_base to
            route to the right endpoint.
          </span>
        </div>

        <div className="opt-field">
          <label className="opt-label">Model</label>
          <input
            className="opt-input"
            value={llm.model}
            onChange={(e) => patchLlm('model', e.target.value)}
            placeholder="e.g. qwen3-max, gpt-4o-mini, qwen2.5:7b-instruct"
          />
        </div>

        <div className="opt-field">
          <label className="opt-label">API base URL</label>
          <input
            className="opt-input"
            value={llm.api_base}
            onChange={(e) => patchLlm('api_base', e.target.value)}
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
          />
          <span className="opt-hint">
            Leave empty for OpenAI default. For Ollama use
            http://localhost:11434/v1.
          </span>
        </div>

        <div className="opt-field">
          <label className="opt-label">API key env var</label>
          <input
            className="opt-input"
            value={llm.api_key_env}
            onChange={(e) => patchLlm('api_key_env', e.target.value)}
            placeholder="e.g. DASHSCOPE_API_KEY"
            list="opt-api-key-envs"
          />
          <datalist id="opt-api-key-envs">
            {llm.allowed_api_key_envs.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
          <span className="opt-hint">
            Must be one of the Agent's allowlisted env names.
            {llm.api_key_has_value && ' (currently populated)'}
          </span>
        </div>

        <div className="opt-field">
          <label className="opt-label">
            API key {llm.api_key_has_value ? '(leave empty to keep existing)' : ''}
          </label>
          <input
            className="opt-input"
            type="password"
            autoComplete="new-password"
            value={llm.api_key}
            onChange={(e) => patchLlm('api_key', e.target.value)}
            placeholder={llm.api_key_has_value ? '\u2022\u2022\u2022\u2022\u2022\u2022 (stored)' : 'sk-...'}
          />
          <span className="opt-hint">
            Stored on-disk in ~/.paperprism/secrets.env (mode 600). Never sent
            anywhere except to the local Agent.
          </span>
        </div>

        <div className="opt-grid-2">
          <div className="opt-field">
            <label className="opt-label">Temperature</label>
            <input
              className="opt-input"
              type="number" step="0.1" min="0" max="2"
              value={llm.temperature}
              onChange={(e) => patchLlm('temperature', Number(e.target.value))}
            />
          </div>
          <div className="opt-field">
            <label className="opt-label">Max output tokens</label>
            <input
              className="opt-input"
              type="number" min="1"
              value={llm.max_output_tokens}
              onChange={(e) => patchLlm('max_output_tokens', Number(e.target.value))}
            />
          </div>
          <div className="opt-field">
            <label className="opt-label">Timeout (s)</label>
            <input
              className="opt-input"
              type="number" min="1"
              value={llm.timeout_seconds}
              onChange={(e) => patchLlm('timeout_seconds', Number(e.target.value))}
            />
          </div>
          <div className="opt-field">
            <label className="opt-label">Max retries</label>
            <input
              className="opt-input"
              type="number" min="0"
              value={llm.max_retries}
              onChange={(e) => patchLlm('max_retries', Number(e.target.value))}
            />
          </div>
        </div>

        <div className="opt-row">
          <button
            className="opt-btn primary"
            onClick={onSaveLlm}
            disabled={llmBusy !== ''}
          >
            {llmBusy === 'saving' ? 'Saving...' : 'Save LLM config'}
          </button>
          <button
            className="opt-btn"
            onClick={loadLlm}
            disabled={llmBusy !== ''}
          >
            {llmBusy === 'loading' ? 'Loading...' : 'Reload from Agent'}
          </button>
          <button
            className="opt-btn"
            onClick={onTestLlm}
            disabled={llmBusy !== ''}
          >
            {llmBusy === 'testing' ? 'Testing...' : 'Test LLM'}
          </button>
          {llmStatus && (
            <span
              className={
                llmStatus.startsWith('OK') || llmStatus.startsWith('Saved') || llmStatus.startsWith('Loaded')
                  ? 'opt-status-ok'
                  : llmStatus.startsWith('Testing')
                    ? ''
                    : 'opt-status-err'
              }
            >
              {llmStatus}
            </span>
          )}
        </div>
      </details>
    </div>
  );
}
