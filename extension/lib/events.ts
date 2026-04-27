/**
 * In-memory log of recent archive events, surfaced in the popup.
 * Kept in chrome.storage.local so it survives service-worker restarts.
 */

import type { ArxivId } from './arxiv';

export type EventStatus =
  | 'detected'
  | 'redirected'
  | 'downloaded'
  | 'agent-sent'
  | 'agent-ok'
  | 'agent-failed'
  | 'skipped';

export interface ArchiveEvent {
  id: string;
  arxivId: string;
  title?: string;
  status: EventStatus;
  message?: string;
  sourceUrl: string;
  /** Absolute filesystem path where Chrome saved the PDF. */
  downloadPath?: string;
  /** Absolute path inside the hidden vault where the Agent placed a copy. */
  vaultPath?: string;
  downloadId?: number;
  updatedAt: string;
}

const KEY = 'paperprism.events.v1';
const MAX = 30;

export async function listEvents(): Promise<ArchiveEvent[]> {
  const got = await chrome.storage.local.get(KEY);
  return ((got[KEY] as ArchiveEvent[] | undefined) ?? []).slice();
}

export async function upsertEvent(
  id: string,
  patch: Partial<ArchiveEvent> & { arxivId: string; sourceUrl: string },
): Promise<ArchiveEvent> {
  const events = await listEvents();
  const idx = events.findIndex((e) => e.id === id);
  const now = new Date().toISOString();
  let next: ArchiveEvent;

  if (idx >= 0) {
    next = { ...events[idx], ...patch, id, updatedAt: now };
    events[idx] = next;
  } else {
    next = {
      status: 'detected',
      ...patch,
      id,
      updatedAt: now,
    } as ArchiveEvent;
    events.unshift(next);
  }

  const trimmed = events.slice(0, MAX);
  await chrome.storage.local.set({ [KEY]: trimmed });
  return next;
}

export function eventIdFor(arxivId: ArxivId, downloadId?: number): string {
  return downloadId != null
    ? `dl:${downloadId}`
    : `id:${arxivId.fullId}:${Date.now()}`;
}

export async function clearEvents(): Promise<void> {
  await chrome.storage.local.remove(KEY);
}
