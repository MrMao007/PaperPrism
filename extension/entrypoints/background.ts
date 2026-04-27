/**
 * Background service worker.
 *
 * Behaviour (per user requirement):
 *  - Let Chrome save arxiv PDFs to the default Downloads folder, *exactly
 *    as the user expects*. We do NOT rewrite the filename.
 *  - Once the download finishes, fetch the absolute on-disk path from
 *    chrome.downloads and forward it to the local Agent, which is
 *    responsible for copying the file into the hidden vault
 *    (e.g. `~/.paperprism/vault/...`) and running LLM classification.
 *  - The Agent is the only component that writes to the hidden vault;
 *    the extension cannot access arbitrary filesystem paths.
 *
 * Events emitted to the Agent:
 *   - archive.requested  (on download start)   -> prefetch arxiv metadata
 *   - archive.completed  (on download finish)  -> copy + classify
 */

import { defineBackground } from 'wxt/sandbox';
import {
  parseArxivIdFromString,
  isArxivPdfUrl,
  absUrlFromId,
  type ArxivId,
} from '@/lib/arxiv';
import {
  sendIngest,
  pingAgent,
  AgentUnavailableError,
  type IngestRequest,
} from '@/lib/agent';
import { loadSettings } from '@/lib/settings';
import {
  upsertEvent,
  eventIdFor,
  type EventStatus,
} from '@/lib/events';

interface PendingDownload {
  eventId: string;
  arxivId: ArxivId;
  sourceUrl: string;
}

// Best-effort correlation map. The persistent source of truth is the
// events store in chrome.storage.local.
const pending = new Map<number, PendingDownload>();

export default defineBackground(() => {
  registerDownloadHooks();
  registerMessageBridge();
  registerInstallHook();
});

function registerInstallHook(): void {
  chrome.runtime.onInstalled.addListener((details) => {
    if (details.reason === 'install') {
      chrome.runtime.openOptionsPage?.();
    }
  });
}

function registerDownloadHooks(): void {
  // Detect an arxiv download the instant Chrome creates the download entry.
  // We do NOT touch the filename here -- the file lands in the default
  // Downloads folder, exactly as the user expects.
  chrome.downloads.onCreated.addListener((item) => {
    handleDownloadCreated(item).catch((err) => {
      console.warn('[PaperPrism] onCreated error', err);
    });
  });

  chrome.downloads.onChanged.addListener((delta) => {
    if (!delta.state || delta.state.current !== 'complete') return;
    handleDownloadCompleted(delta.id).catch((err) => {
      console.warn('[PaperPrism] onChanged(complete) error', err);
    });
  });
}

async function handleDownloadCreated(
  item: chrome.downloads.DownloadItem,
): Promise<void> {
  const url = item.finalUrl || item.url || '';
  if (!isArxivPdfUrl(url)) return;

  const arxivId =
    parseArxivIdFromString(url) ?? parseArxivIdFromString(item.filename || '');
  if (!arxivId) return;

  const { archiveEnabled, vaultPathHint } = await loadSettings();
  if (!archiveEnabled) return;

  const eventId = eventIdFor(arxivId, item.id);
  pending.set(item.id, { eventId, arxivId, sourceUrl: url });

  await logEvent(eventId, arxivId, url, 'detected', {
    downloadId: item.id,
    message: `arxiv download detected (${arxivId.fullId})`,
  });

  // Fire-and-forget "archive.requested" so the Agent can prefetch arxiv
  // API metadata in parallel with the download itself.
  void notifyAgent(
    {
      event: 'archive.requested',
      arxivId,
      sourceUrl: url,
      vaultPathHint,
      downloadId: item.id,
      triggerClassification: false,
      absUrl: absUrlFromId(arxivId),
      emittedAt: new Date().toISOString(),
    },
    eventId,
    arxivId,
  );
}

async function handleDownloadCompleted(downloadId: number): Promise<void> {
  const info = pending.get(downloadId);
  if (!info) return;
  pending.delete(downloadId);

  // DownloadItem.filename is empty in onCreated; we must query it now.
  const [item] = await chrome.downloads.search({ id: downloadId });
  const downloadPath = item?.filename;
  if (!downloadPath) {
    await logEvent(info.eventId, info.arxivId, info.sourceUrl, 'agent-failed', {
      downloadId,
      message: 'Chrome did not report a filesystem path for the download',
    });
    return;
  }

  const { vaultPathHint } = await loadSettings();

  await logEvent(info.eventId, info.arxivId, info.sourceUrl, 'downloaded', {
    downloadPath,
    downloadId,
    message: `Saved to ${downloadPath}, asking Agent to mirror into vault`,
  });

  await notifyAgent(
    {
      event: 'archive.completed',
      arxivId: info.arxivId,
      sourceUrl: info.sourceUrl,
      downloadPath,
      vaultPathHint,
      downloadId,
      triggerClassification: true,
      absUrl: absUrlFromId(info.arxivId),
      emittedAt: new Date().toISOString(),
    },
    info.eventId,
    info.arxivId,
  );
}

async function notifyAgent(
  req: IngestRequest,
  eventId: string,
  arxivId: ArxivId,
): Promise<void> {
  try {
    const res = await sendIngest(req);
    await logEvent(eventId, arxivId, req.sourceUrl, 'agent-ok', {
      downloadPath: req.downloadPath,
      vaultPath: res.vaultPath,
      downloadId: req.downloadId,
      message: res.message ?? res.status ?? 'Agent accepted',
    });
    if (req.event === 'archive.completed') {
      await maybeNotify(
        'PaperPrism',
        `Archived ${arxivId.fullId}${res.vaultPath ? ` -> ${res.vaultPath}` : ''}`,
      );
    }
  } catch (err) {
    const reason =
      err instanceof AgentUnavailableError
        ? 'Local Agent not running. The PDF is in your Downloads folder; start the Agent to mirror it into the vault.'
        : (err as Error).message;
    await logEvent(eventId, arxivId, req.sourceUrl, 'agent-failed', {
      downloadPath: req.downloadPath,
      downloadId: req.downloadId,
      message: reason,
    });
    if (req.event === 'archive.completed') {
      await maybeNotify('PaperPrism', reason);
    }
  }
}

function registerMessageBridge(): void {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      switch (msg?.type) {
        case 'ping-agent': {
          sendResponse({ ok: await pingAgent() });
          return;
        }
        case 'manual-archive': {
          const url = msg.pdfUrl as string;
          if (!url || !isArxivPdfUrl(url)) {
            sendResponse({ ok: false, error: 'Not an arxiv PDF URL' });
            return;
          }
          try {
            const id = await chrome.downloads.download({ url });
            sendResponse({ ok: true, downloadId: id });
          } catch (err) {
            sendResponse({ ok: false, error: (err as Error).message });
          }
          return;
        }
        default:
          sendResponse({ ok: false, error: 'Unknown message type' });
      }
    })();
    return true;
  });
}

async function maybeNotify(title: string, message: string): Promise<void> {
  const { notifyEnabled } = await loadSettings();
  if (!notifyEnabled) return;
  try {
    const iconUrl = chrome.runtime.getURL('icon/128.png');
    chrome.notifications.create(
      {
        type: 'basic',
        iconUrl,
        title,
        message,
      },
      () => {
        void chrome.runtime.lastError;
      },
    );
  } catch {
    // swallow: notifications permission may not be granted in dev.
  }
}

async function logEvent(
  eventId: string,
  arxivId: ArxivId,
  sourceUrl: string,
  status: EventStatus,
  extra: {
    downloadPath?: string;
    vaultPath?: string;
    downloadId?: number;
    message?: string;
  } = {},
): Promise<void> {
  await upsertEvent(eventId, {
    arxivId: arxivId.fullId,
    sourceUrl,
    status,
    ...extra,
  });
}
