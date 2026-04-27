import { useEffect, useState } from 'react';
import {
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  type Settings,
} from '@/lib/settings';

export default function App() {
  const [s, setS] = useState<Settings>(DEFAULT_SETTINGS);
  const [status, setStatus] = useState<string>('');
  const [agent, setAgent] = useState<'unknown' | 'online' | 'offline'>('unknown');

  useEffect(() => {
    void (async () => setS(await loadSettings()))();
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

  return (
    <div className="opt-wrap">
      <h1 className="opt-title">PaperPrism</h1>
      <p className="opt-sub">
        Local arxiv paper organizer. Papers are saved to your normal Downloads
        folder; the local Agent mirrors each finished download into a hidden
        workspace where it runs metadata extraction and LLM classification.
      </p>

      <div className="opt-card">
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
      </div>
    </div>
  );
}
