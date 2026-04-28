import { useEffect, useMemo, useState } from 'react';
import { listEvents, clearEvents, type ArchiveEvent } from '@/lib/events';
import { getDashboardUrl } from '@/lib/agent';

type AgentStatus = 'unknown' | 'online' | 'offline';

export default function App() {
  const [events, setEvents] = useState<ArchiveEvent[]>([]);
  const [agent, setAgent] = useState<AgentStatus>('unknown');
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    void refresh();
    void probeAgent();
    const t = setInterval(() => {
      void refresh();
      void probeAgent();
      setNow(Date.now());
    }, 2500);
    return () => clearInterval(t);
  }, []);

  async function refresh() {
    setEvents(await listEvents());
  }

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
    await chrome.tabs.create({ url, active: true });
    window.close();
  }

  function openOptions() {
    chrome.runtime.openOptionsPage?.();
  }

  async function onClear() {
    await clearEvents();
    await refresh();
  }

  const agentLabel = agent === 'online' ? 'Online' : agent === 'offline' ? 'Offline' : '…';
  const agentClass = agent === 'online' ? 'ok' : agent === 'offline' ? 'err' : '';

  const visibleEvents = useMemo(() => events.slice(0, 10), [events]);

  return (
    <div className="pp-root">
      {/* Hero */}
      <div className="pp-hero">
        <img className="pp-logo" src="/icon/128.png" alt="PaperPrism" />
        <div className="pp-hero-text">
          <div className="pp-title">PaperPrism</div>
          <div className="pp-subtitle">Local-first paper library</div>
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
          onClick={openOptions}
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
            <button className="pp-btn primary" onClick={openOptions}>
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

      {/* Activity */}
      {agent !== 'offline' && (
        <>
          <div className="pp-section-title">
            <span>Recent activity</span>
            {events.length > 0 && (
              <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 500 }}>
                {events.length} {events.length === 1 ? 'event' : 'events'}
              </span>
            )}
          </div>

          {visibleEvents.length === 0 ? (
            <div className="pp-empty">
              <IconInbox className="pp-empty-icon" />
              <div>No arxiv downloads yet.</div>
              <div style={{ fontSize: 11 }}>
                Download a PDF from arxiv.org to get started.
              </div>
            </div>
          ) : (
            <div className="pp-events">
              {visibleEvents.map((e) => (
                <EventRow key={e.id} event={e} now={now} />
              ))}
            </div>
          )}
        </>
      )}

      {/* Footer */}
      <div className="pp-footer">
        <a className="pp-link" onClick={openOptions} role="button" tabIndex={0}>
          <IconSettings />
          Settings
        </a>
        <a className="pp-link" onClick={onClear} role="button" tabIndex={0}>
          <IconTrash />
          Clear log
        </a>
      </div>
    </div>
  );
}

function EventRow({ event, now }: { event: ArchiveEvent; now: number }) {
  const updated = Date.parse(event.updatedAt);
  const rel = formatRelative(now - updated);
  return (
    <div className={`pp-event status-${event.status}`}>
      <div className="pp-event-body">
        <div className="pp-event-head">
          <span className="pp-event-id">{event.arxivId}</span>
          <span className="pp-event-time">{rel}</span>
        </div>
        {event.title && <div className="pp-event-title">{event.title}</div>}
        <div className="pp-event-status-label">{prettyStatus(event.status)}</div>
        {event.message && <div className="pp-event-msg">{event.message}</div>}
      </div>
    </div>
  );
}

function prettyStatus(s: ArchiveEvent['status']): string {
  switch (s) {
    case 'agent-ok': return 'Archived';
    case 'agent-sent': return 'Sending';
    case 'agent-failed': return 'Failed';
    case 'downloaded': return 'Downloaded';
    case 'redirected': return 'Redirected';
    case 'skipped': return 'Skipped';
    case 'detected': return 'Detected';
  }
}

function formatRelative(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return 'just now';
  const sec = Math.floor(ms / 1000);
  if (sec < 10) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return `${Math.floor(day / 7)}w ago`;
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

function IconInbox(props: { className?: string }) {
  return (
    <svg className={props.className ?? 'pp-icon'} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg className="pp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="m19 6-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}
