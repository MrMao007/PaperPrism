import type { ArxivId } from './arxiv';
import { loadSettings } from './settings';

/**
 * Thin HTTP client for talking to the local PaperPrism Agent.
 * The Agent is responsible for: moving files from the inbox to the vault,
 * extracting metadata (arxiv API + PDF), running LLM classification,
 * and building symlinks across views.
 *
 * The extension never touches the filesystem directly -- Chrome doesn't
 * grant that capability anyway. We only send events.
 */

export interface IngestRequest {
  event: 'archive.requested' | 'archive.completed';
  arxivId: ArxivId;
  /** URL the PDF was originally downloaded from. */
  sourceUrl: string;
  /**
   * Absolute filesystem path where Chrome actually saved the PDF.
   * Populated only on `archive.completed`. The Agent reads from this
   * path and copies into the hidden vault.
   */
  downloadPath?: string;
  /**
   * Preferred destination for the copy, hinted by the UI (e.g.
   * `~/.paperprism/vault`). The Agent may override.
   */
  vaultPathHint?: string;
  /** Chrome download id, used to correlate events. */
  downloadId?: number;
  /** When true, the Agent should kick off LLM classification immediately. */
  triggerClassification: boolean;
  /** Optional abstract page URL for richer metadata harvesting. */
  absUrl?: string;
  /** ISO 8601 timestamp of when the event was emitted. */
  emittedAt: string;
}

export interface IngestResponse {
  accepted: boolean;
  /** Absolute path inside the vault where the Agent placed the copy. */
  vaultPath?: string;
  /** One of: "queued", "classified", "needs_review". */
  status?: string;
  message?: string;
}

export class AgentUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AgentUnavailableError';
  }
}

async function authedFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const { agentBaseUrl, agentToken } = await loadSettings();
  const headers = new Headers(init.headers);
  headers.set('Content-Type', 'application/json');
  if (agentToken) headers.set('X-PaperPrism-Token', agentToken);

  const url = `${agentBaseUrl.replace(/\/$/, '')}${path}`;
  try {
    const res = await fetch(url, { ...init, headers });
    return res;
  } catch (err) {
    throw new AgentUnavailableError(
      `Local Agent unreachable at ${url}: ${(err as Error).message}`,
    );
  }
}

export async function pingAgent(): Promise<boolean> {
  try {
    const res = await authedFetch('/api/health', { method: 'GET' });
    return res.ok;
  } catch {
    return false;
  }
}

export async function sendIngest(
  req: IngestRequest,
): Promise<IngestResponse> {
  const res = await authedFetch('/api/ingest', {
    method: 'POST',
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Agent rejected ingest: ${res.status} ${text}`);
  }
  return (await res.json()) as IngestResponse;
}

export async function getDashboardUrl(): Promise<string> {
  return chrome.runtime.getURL('/dashboard.html');
}

// --------------- Dashboard API ---------------

export interface PaperClassifications {
  [dimName: string]: string[];
}

export interface PaperItem {
  id: number;
  full_id: string;
  arxiv_id: string;
  title: string | null;
  first_author: string | null;
  authors: string[];
  arxiv_categories: string[];
  affiliations: string[];
  abstract: string | null;
  venue: string | null;
  code_url: string | null;
  published_at: string | null;
  ingested_at: string;
  enriched_at: string | null;
  classified_at: string | null;
  abs_url: string | null;
  classifications: PaperClassifications;
}

export interface PapersResponse {
  items: PaperItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface FetchPapersParams {
  limit?: number;
  offset?: number;
  q?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  domain?: string;
  affiliations?: string;
}

export async function fetchPapers(
  params: FetchPapersParams = {},
): Promise<PapersResponse> {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set('limit', String(params.limit));
  if (params.offset != null) qs.set('offset', String(params.offset));
  if (params.q) qs.set('q', params.q);
  if (params.sort) qs.set('sort', params.sort);
  if (params.order) qs.set('order', params.order);
  if (params.domain) qs.set('domain', params.domain);
  if (params.affiliations) qs.set('affiliations', params.affiliations);
  const res = await authedFetch(`/api/papers?${qs.toString()}`, { method: 'GET' });
  if (!res.ok) {
    throw new Error(`Failed to fetch papers: ${res.status}`);
  }
  return (await res.json()) as PapersResponse;
}

export type DimensionValues = Record<string, string[]>;

export async function fetchDimensionValues(): Promise<DimensionValues> {
  const res = await authedFetch('/api/dimensions/values', { method: 'GET' });
  if (!res.ok) {
    throw new Error(`Failed to fetch dimension values: ${res.status}`);
  }
  return (await res.json()) as DimensionValues;
}

export interface DeletePaperResponse {
  deleted: boolean;
  paper_id: number;
  full_id: string | null;
  files_removed: boolean;
}

export async function deletePaper(
  paperId: number,
  removeFiles: boolean = true,
): Promise<DeletePaperResponse> {
  const qs = new URLSearchParams();
  qs.set('remove_files', removeFiles ? 'true' : 'false');
  const res = await authedFetch(
    `/api/papers/${paperId}?${qs.toString()}`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to delete paper ${paperId}: ${res.status} ${text}`);
  }
  return (await res.json()) as DeletePaperResponse;
}

/**
 * Fetch the PDF bytes of a paper (respects auth token) and open the
 * resulting blob URL in a new browser tab.  Returns the paper id on
 * success; throws if the agent is unreachable or returns non-2xx.
 */
export async function openPaperPdf(paperId: number): Promise<void> {
  const res = await authedFetch(`/api/papers/${paperId}/pdf`, { method: 'GET' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to load pdf ${paperId}: ${res.status} ${text}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  // Release the object URL after the new tab has had a chance to load.
  window.open(url, '_blank', 'noopener,noreferrer');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
