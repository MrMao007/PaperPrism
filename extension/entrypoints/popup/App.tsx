import { useEffect, useState } from 'react';
import { listEvents, clearEvents, type ArchiveEvent } from '@/lib/events';
import { getDashboardUrl } from '@/lib/agent';

type AgentStatus = 'unknown' | 'online' | 'offline';

export default function App() {
  const [events, setEvents] = useState<ArchiveEvent[]>([]);
  const [agent, setAgent] = useState<AgentStatus>('unknown');

  useEffect(() => {
    void refresh();
    void probeAgent();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, []);

  async function refresh() {
    setEvents(await listEvents());
  }

  async function probeAgent() {
    const res = await chrome.runtime.sendMessage({ type: 'ping-agent' });
    setAgent(res?.ok ? 'online' : 'offline');
  }

  async function openDashboard() {
    const url = await getDashboardUrl();
    await chrome.tabs.create({ url, active: true });
    window.close();
  }

  async function archiveActiveTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) return;
    // Convert /abs/xxx -> /pdf/xxx when needed; background will validate.
    const pdfUrl = tab.url.replace('/abs/', '/pdf/');
    await chrome.runtime.sendMessage({ type: 'manual-archive', pdfUrl });
    await refresh();
  }

  function openOptions() {
    chrome.runtime.openOptionsPage?.();
  }

  async function onClear() {
    await clearEvents();
    await refresh();
  }

  return (
    <div className="pp-root">
      <div className="pp-header">
        <div className="pp-title">PaperPrism</div>
        <span className={`pp-badge ${agent === 'online' ? 'ok' : agent === 'offline' ? 'err' : ''}`}>
          Agent: {agent}
        </span>
      </div>

      <div className="pp-row">
        <button className="pp-btn primary" onClick={archiveActiveTab}>
          Archive current tab
        </button>
        <button className="pp-btn" onClick={openDashboard}>
          Dashboard
        </button>
      </div>

      {agent === 'offline' && (
        <div className="pp-offline-hint">
          <div className="pp-offline-title">Agent not running</div>
          <div className="pp-offline-body">
            PaperPrism needs a small local helper to copy and classify papers.
            Open Settings to run the first-time setup wizard.
          </div>
          <div className="pp-row">
            <button className="pp-btn primary" onClick={openOptions}>
              Open setup
            </button>
            <button className="pp-btn" onClick={probeAgent}>
              Retry
            </button>
          </div>
        </div>
      )}

      <div className="pp-section-title">Recent events</div>
      {events.length === 0 ? (
        <div className="pp-empty">No arxiv downloads detected yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
          {events.slice(0, 10).map((e) => (
            <div key={e.id} className={`pp-event status-${e.status}`}>
              <div className="pp-event-head">
                <span className="pp-event-id">{e.arxivId}</span>
                <span className="pp-status">{e.status}</span>
              </div>
              {e.message && <span className="pp-event-msg">{e.message}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="pp-footer">
        <a className="pp-link" onClick={openOptions}>Settings</a>
        <a className="pp-link" onClick={onClear}>Clear log</a>
      </div>
    </div>
  );
}
