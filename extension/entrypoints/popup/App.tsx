import { useEffect, useState } from 'react';
import { getDashboardUrl } from '@/lib/agent';

type AgentStatus = 'unknown' | 'online' | 'offline';

export default function App() {
  const [agent, setAgent] = useState<AgentStatus>('unknown');

  useEffect(() => {
    void probeAgent();
    const t = setInterval(() => void probeAgent(), 2500);
    return () => clearInterval(t);
  }, []);

  async function probeAgent() {
    try {
      const res = await chrome.runtime.sendMessage({ type: 'ping-agent' });
      setAgent(res?.ok ? 'online' : 'offline');
    } catch {
      setAgent('offline');
    }
  }

  async function openDashboard() {
    const url = await getDashboardUrl();
    window.open(url, '_blank', 'noopener,noreferrer');
    window.close();
  }

  const agentLabel = agent === 'online' ? 'Online' : agent === 'offline' ? 'Offline' : '…';
  const agentClass  = agent === 'online' ? 'ok'     : agent === 'offline' ? 'err'     : '';

  return (
    <div className="pp-root">
      {/* Hero */}
      <div className="pp-hero">
        <img className="pp-logo" src="/icon/128.png" alt="PaperPrism" />
        <div className="pp-hero-text">
          <div className="pp-title">
            Paper<span className="pp-prism">Prism</span>
          </div>
          <div className="pp-subtitle">Local Atlas · 127.0.0.1</div>
        </div>
        <span
          className={`pp-status-pill ${agentClass}`}
          title={agent === 'online' ? 'Connected to local Agent' : 'Local Agent unreachable'}
        >
          <span className="pp-status-dot" />
          {agentLabel}
        </span>
      </div>

      {/* Primary actions */}
      <div className="pp-actions">
        <button className="pp-btn primary" onClick={openDashboard}>
          <IconLayers />
          Open Dashboard
        </button>
        <button
          className="pp-btn icon-only"
          onClick={() => chrome.runtime.openOptionsPage?.()}
          aria-label="Settings"
          title="Settings"
        >
          <IconSettings />
        </button>
      </div>

      {/* Offline banner */}
      {agent === 'offline' && (
        <div className="pp-offline" role="alert">
          <div className="pp-offline-head">
            <IconAlert className="pp-offline-icon" />
            <div className="pp-offline-title">Agent not reachable</div>
          </div>
          <div className="pp-offline-body">
            PaperPrism needs a small local helper running on your machine to
            archive and classify papers. Run the first-time setup wizard to
            install it.
          </div>
          <div className="pp-offline-actions">
            <button className="pp-btn primary" onClick={() => chrome.runtime.openOptionsPage?.()}>
              <IconSparkles />
              Open setup
            </button>
            <button className="pp-btn" onClick={probeAgent}>
              <IconRefresh />
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="pp-footer">
        <a
          className="pp-link"
          href="https://github.com/MrMao007/PaperPrism"
          target="_blank"
          rel="noopener noreferrer"
        >
          <IconGitHub />
          GitHub
        </a>
      </div>
    </div>
  );
}

/* ---------- Inline SVG icons ---------- */

function IconLayers() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2 2 7l10 5 10-5-10-5Z" />
      <path d="m2 17 10 5 10-5" />
      <path d="m2 12 10 5 10-5" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IconAlert(props: { className?: string }) {
  return (
    <svg className={props.className ?? 'pp-icon'} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

function IconSparkles() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
    </svg>
  );
}

function IconRefresh() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

function IconGitHub() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.02 10.02 0 0 0 22 12.017C22 6.484 17.522 2 12 2Z" />
    </svg>
  );
}
