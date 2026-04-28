import { useEffect, useState } from 'react';
import {
  pingAgent,
  saveLlmConfig,
  testLlmConfig,
  getDashboardUrl,
} from '@/lib/agent';
import {
  PROVIDER_PRESETS,
  detectOllama,
  findPreset,
  type ProviderPreset,
} from '@/lib/providers';

interface WizardProps {
  onComplete: () => void;
}

type Step = 1 | 2 | 3 | 4;

/**
 * First-run setup wizard shown when `settings.wizardCompleted` is false.
 *
 * Steps:
 *   1. Connect Agent (probe + retry + download help)
 *   2. Pick provider (auto-highlights Ollama if detected locally)
 *   3. Enter API key (skipped for keyless presets), save + test
 *   4. Done → open Dashboard
 *
 * The wizard writes llm.yaml and (optionally) secrets.env via the Agent's
 * /api/llm/config endpoint, so no user terminal is required.
 */
export default function Wizard({ onComplete }: WizardProps) {
  const [step, setStep] = useState<Step>(1);
  const [agentOk, setAgentOk] = useState<boolean | null>(null);
  const [ollamaSeen, setOllamaSeen] = useState(false);
  const [selectedId, setSelectedId] = useState<string>('dashscope');
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState<'' | 'saving' | 'testing'>('');
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState<string>('');

  // Probe agent + ollama when the wizard mounts / step returns to 1.
  useEffect(() => {
    if (step !== 1) return;
    void (async () => {
      const [a, o] = await Promise.all([pingAgent(), detectOllama()]);
      setAgentOk(a);
      setOllamaSeen(o);
      if (o) setSelectedId('ollama');
    })();
  }, [step]);

  const preset = findPreset(selectedId);

  async function saveAndTest(p: ProviderPreset, keyPlain: string) {
    setError('');
    setTestResult('');
    setBusy('saving');
    try {
      await saveLlmConfig({
        provider: p.provider,
        model: p.model,
        api_base: p.api_base || null,
        api_key_env: p.api_key_env || null,
        temperature: 0,
        max_output_tokens: 600,
        timeout_seconds: 60,
        max_retries: 2,
        abstract_char_limit: 2000,
        pdf_head_char_limit: 1500,
        auto_tag_on_ingest: true,
        ...(keyPlain && p.api_key_env ? { api_key: keyPlain } : {}),
      });
    } catch (err) {
      setError(`Save failed: ${(err as Error).message}`);
      setBusy('');
      return;
    }
    setBusy('testing');
    try {
      const res = await testLlmConfig();
      if (res.ok) {
        setTestResult(`OK: ${res.provider_label} responded with ${res.sample}`);
        setApiKey(''); // don't keep plain key in memory longer than needed
        setStep(4);
      } else {
        setError(res.error || 'LLM test failed');
      }
    } catch (err) {
      setError(`Test failed: ${(err as Error).message}`);
    } finally {
      setBusy('');
    }
  }

  async function openDashboardAndClose() {
    onComplete();
    const url = await getDashboardUrl();
    await chrome.tabs.create({ url, active: true });
  }

  return (
    <div className="opt-card wiz-card">
      <div className="wiz-header">
        <h2 className="opt-section-title">Welcome to PaperPrism</h2>
        <span className="wiz-step">Step {step} of 4</span>
      </div>

      <div className="wiz-progress">
        <span className={`wiz-dot ${step >= 1 ? 'on' : ''}`} />
        <span className={`wiz-dot ${step >= 2 ? 'on' : ''}`} />
        <span className={`wiz-dot ${step >= 3 ? 'on' : ''}`} />
        <span className={`wiz-dot ${step >= 4 ? 'on' : ''}`} />
      </div>

      {step === 1 && (
        <div className="wiz-step-body">
          <p className="opt-sub">
            First, make sure the local Agent is running. It handles file copy,
            PDF parsing and LLM classification.
          </p>
          <div className="wiz-probe">
            <span
              className={
                agentOk === true
                  ? 'opt-status-ok'
                  : agentOk === false
                    ? 'opt-status-err'
                    : ''
              }
            >
              {agentOk === null
                ? 'Probing http://127.0.0.1:17321 ...'
                : agentOk
                  ? '✅ Agent is reachable.'
                  : '❌ Agent unreachable.'}
            </span>
          </div>
          {agentOk === false && (
            <div className="wiz-help">
              <p className="opt-hint">
                Install and start the Agent in a terminal:
              </p>
              <pre className="wiz-pre">
{`cd agent
pip install -e .
paperprism-agent install   # register as launchd auto-start
paperprism-agent restart`}
              </pre>
              <p className="opt-hint">
                Already running on a different port? Open <b>Advanced</b> below
                and set <code>Local Agent base URL</code>.
              </p>
            </div>
          )}
          <div className="opt-row">
            <button
              className="opt-btn"
              onClick={async () => setAgentOk(await pingAgent())}
            >
              Retry probe
            </button>
            <button
              className="opt-btn primary"
              disabled={!agentOk}
              onClick={() => setStep(2)}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="wiz-step-body">
          <p className="opt-sub">
            Pick a model provider. This fills <code>api_base</code> and
            <code> api_key_env</code> for you; you can tweak anything later.
          </p>
          {ollamaSeen && (
            <p className="opt-status-ok">
              🐑 Ollama detected on localhost:11434 — recommended for zero-cost
              local setup.
            </p>
          )}
          <div className="wiz-provider-list">
            {PROVIDER_PRESETS.map((p) => (
              <label
                key={p.id}
                className={`wiz-provider ${selectedId === p.id ? 'active' : ''}`}
              >
                <input
                  type="radio"
                  name="provider"
                  checked={selectedId === p.id}
                  onChange={() => setSelectedId(p.id)}
                />
                <div className="wiz-provider-main">
                  <div className="wiz-provider-label">
                    {p.label}
                    {!p.needsKey && <span className="wiz-tag">免 key</span>}
                    {p.id === 'ollama' && ollamaSeen && (
                      <span className="wiz-tag ok">已检测到</span>
                    )}
                  </div>
                  <div className="wiz-provider-desc">{p.description}</div>
                  <div className="wiz-provider-meta">
                    <code>{p.model}</code>
                  </div>
                </div>
              </label>
            ))}
          </div>
          <div className="opt-row">
            <button className="opt-btn" onClick={() => setStep(1)}>
              Back
            </button>
            <button
              className="opt-btn primary"
              onClick={() => setStep(preset?.needsKey ? 3 : 3)}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {step === 3 && preset && (
        <div className="wiz-step-body">
          <p className="opt-sub">
            {preset.needsKey ? (
              <>
                Paste your <b>{preset.label}</b> API key. It will be saved to
                <code> ~/.paperprism/secrets.env</code> (mode 600) and injected
                into the running Agent.
              </>
            ) : (
              <>
                No API key needed for <b>{preset.label}</b>. We will just save
                the configuration and test it.
              </>
            )}
          </p>
          {preset.needsKey && (
            <div className="opt-field">
              <label className="opt-label">API key</label>
              <input
                className="opt-input"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
              />
              {preset.keyHelpUrl && (
                <span className="opt-hint">
                  Need one?{' '}
                  <a
                    className="opt-link"
                    href={preset.keyHelpUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Get an API key →
                  </a>
                </span>
              )}
            </div>
          )}
          {error && <div className="opt-status-err">{error}</div>}
          {busy !== '' && (
            <div className="opt-hint">
              {busy === 'saving'
                ? 'Writing llm.yaml + secrets.env...'
                : 'Running a tiny chat request to verify...'}
            </div>
          )}
          <div className="opt-row">
            <button className="opt-btn" onClick={() => setStep(2)}>
              Back
            </button>
            <button
              className="opt-btn primary"
              disabled={busy !== '' || (preset.needsKey && !apiKey.trim())}
              onClick={() => saveAndTest(preset, apiKey.trim())}
            >
              {busy !== '' ? 'Working...' : 'Save & Test'}
            </button>
          </div>
        </div>
      )}

      {step === 4 && (
        <div className="wiz-step-body">
          <p className="opt-status-ok">🎉 {testResult || 'All set.'}</p>
          <p className="opt-sub">
            You can open the Dashboard to browse, filter and delete papers.
            Further settings (API base URL, model, retries) are editable below
            under <b>Advanced</b>.
          </p>
          <div className="opt-row">
            <button className="opt-btn" onClick={onComplete}>
              Stay on Options
            </button>
            <button className="opt-btn primary" onClick={openDashboardAndClose}>
              Open Dashboard
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
