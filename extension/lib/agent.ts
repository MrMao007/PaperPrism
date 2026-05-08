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

/** Response shape for POST /api/ingest/upload (bulk folder import). */
export interface UploadIngestResponse {
  accepted: boolean;
  paperId?: number;
  fullId?: string;
  arxivId?: string;
  duplicate: boolean;
  vaultPath?: string;
  title?: string;
  /** 'queued' | 'duplicate' | 'rejected' */
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

/**
 * Upload a single PDF (File from a <input type="file" webkitdirectory>)
 * to the Agent. The Agent saves it into the vault, auto-detects an arxiv
 * id if the first page contains one, and enqueues enrich + classify.
 *
 * Notes:
 *   - Do NOT force a Content-Type header; the browser must append the
 *     multipart boundary automatically.
 *   - AbortSignal makes it possible to cancel an in-flight upload when
 *     the user closes the progress modal mid-batch.
 */
export async function uploadPdfToAgent(
  file: File,
  opts: { sourceHint?: string; signal?: AbortSignal } = {},
): Promise<UploadIngestResponse> {
  const { agentBaseUrl, agentToken } = await loadSettings();
  const url = `${agentBaseUrl.replace(/\/$/, '')}/api/ingest/upload`;

  const form = new FormData();
  form.append('file', file, file.name);
  if (opts.sourceHint) form.append('source_hint', opts.sourceHint);

  const headers: Record<string, string> = {};
  if (agentToken) headers['X-PaperPrism-Token'] = agentToken;

  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      body: form,
      headers,
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new AgentUnavailableError(
      `Local Agent unreachable at ${url}: ${(err as Error).message}`,
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Upload failed: ${res.status} ${text}`);
  }
  return (await res.json()) as UploadIngestResponse;
}

export async function getDashboardUrl(): Promise<string> {
  return chrome.runtime.getURL('/dashboard.html');
}

// --------------- Dashboard API ---------------

export interface PaperClassifications {
  [dimName: string]: string[];
}

export interface PaperTag {
  id: number;
  name: string;
  display_name: string | null;
  source: 'llm' | 'user';
  topic_id: number | null;
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
  tags: PaperTag[];
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
  tag?: string;
  topic?: string;
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
  if (params.tag) qs.set('tag', params.tag);
  if (params.topic) qs.set('topic', params.topic);
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

/** Request body for POST /api/events/track. */
export interface TrackEventBody {
  event_type: string;
  subject_type: string;
  subject_id: string;
  actor?: string;
  payload?: Record<string, unknown>;
}

/** Append a single L1 read-behaviour event to the ledger. */
export async function trackEvent(body: TrackEventBody): Promise<void> {
  const res = await authedFetch('/api/events/track', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.warn('trackEvent failed:', res.status, await res.text().catch(() => ''));
  }
}

/**
 * Fetch the PDF bytes of a paper (respects auth token) and open the
 * resulting blob URL in a new browser tab.  Tracks paper.opened and
 * paper.read_session (≥ 30 s) automatically.
 */
export async function openPaperPdf(paperId: number, arxivId: string): Promise<void> {
  const res = await authedFetch(`/api/papers/${paperId}/pdf`, { method: 'GET' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to load pdf ${paperId}: ${res.status} ${text}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  // Open without any feature string so the browser returns a usable WindowProxy.
  const win = window.open(url, '_blank');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);

  // L1: paper.opened
  void trackEvent({
    event_type: 'paper.opened',
    subject_type: 'paper',
    subject_id: arxivId,
    payload: { source: 'dashboard_pdf_button' },
  });

  // L1: paper.read_session (≥ 30 s)
  if (win) {
    const start = Date.now();
    const interval = setInterval(() => {
      try {
        if (win.closed) {
          clearInterval(interval);
          const elapsed = Math.round((Date.now() - start) / 1000);
          if (elapsed >= 30) {
            void trackEvent({
              event_type: 'paper.read_session',
              subject_type: 'paper',
              subject_id: arxivId,
              payload: { duration_seconds: elapsed },
            });
          }
        }
      } catch {
        // Cross-origin WindowProxy access may throw; ignore.
      }
    }, 1000);
  } else {
    console.warn('[PaperPrism] window.open returned null; read_session will not be tracked');
  }
}

// --------------- LLM config API ---------------

export interface LlmConfig {
  version: number;
  provider: string;
  model: string;
  api_base: string;
  api_key_env: string;
  api_key_has_value: boolean;
  temperature: number;
  max_output_tokens: number;
  timeout_seconds: number;
  max_retries: number;
  abstract_char_limit: number;
  pdf_head_char_limit: number;
  auto_tag_on_ingest: boolean;
  feed_categories: string[];
  allowed_api_key_envs: string[];
  path: string;
}

export interface LlmConfigUpdate {
  version?: number;
  provider: string;
  model: string;
  api_base?: string | null;
  api_key_env?: string | null;
  /** Plain-text key; when non-empty, is written into secrets.env
   *  (requires api_key_env to be in the allowlist). */
  api_key?: string;
  temperature?: number;
  max_output_tokens?: number;
  timeout_seconds?: number;
  max_retries?: number;
  abstract_char_limit?: number;
  pdf_head_char_limit?: number;
  auto_tag_on_ingest?: boolean;
  feed_categories?: string[];
}

export interface LlmTestResult {
  ok: boolean;
  provider_label?: string;
  sample?: string;
  error?: string;
}

export async function fetchLlmConfig(): Promise<LlmConfig> {
  const res = await authedFetch('/api/llm/config', { method: 'GET' });
  if (!res.ok) throw new Error(`Failed to load llm config: ${res.status}`);
  return (await res.json()) as LlmConfig;
}

export async function saveLlmConfig(
  cfg: LlmConfigUpdate,
): Promise<{ saved: boolean; secret_written: boolean; path: string }> {
  const res = await authedFetch('/api/llm/config', {
    method: 'PUT',
    body: JSON.stringify(cfg),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to save llm config: ${res.status} ${text}`);
  }
  return (await res.json()) as {
    saved: boolean;
    secret_written: boolean;
    path: string;
  };
}

export async function testLlmConfig(): Promise<LlmTestResult> {
  const res = await authedFetch('/api/llm/test', { method: 'POST' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`LLM test endpoint error: ${res.status} ${text}`);
  }
  return (await res.json()) as LlmTestResult;
}

// --------------- Tags / topics API ---------------

export interface TagSummary {
  id: number;
  name: string;
  display_name: string | null;
  count: number;
  llm_count: number;
  user_count: number;
}

export async function fetchTags(): Promise<TagSummary[]> {
  const res = await authedFetch('/api/tags', { method: 'GET' });
  if (!res.ok) throw new Error(`Failed to fetch tags: ${res.status}`);
  const j = (await res.json()) as { items: TagSummary[] };
  return j.items;
}

export async function fetchPaperTags(paperId: number): Promise<PaperTag[]> {
  const res = await authedFetch(`/api/papers/${paperId}/tags`, { method: 'GET' });
  if (!res.ok) throw new Error(`Failed to fetch tags: ${res.status}`);
  const j = (await res.json()) as { items: PaperTag[] };
  return j.items;
}

export interface PaperTagEditResponse {
  paper_id: number;
  added: number;
  removed: number;
  tags: PaperTag[];
}

export async function editPaperTags(
  paperId: number,
  payload: { add?: string[]; remove?: string[] },
): Promise<PaperTagEditResponse> {
  const res = await authedFetch(`/api/papers/${paperId}/tags`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to edit tags: ${res.status} ${text}`);
  }
  return (await res.json()) as PaperTagEditResponse;
}

export interface TopicSummary {
  id: number;
  slug: string;
  name: string;
  summary: string | null;
  model: string | null;
  source_job_id: string | null;
  created_at: string;
  paper_count: number;
  top_tags: string[];
}

export interface TopicDetail extends TopicSummary {
  is_archived: number;
  papers: PaperItem[];
}

export async function fetchTopics(): Promise<TopicSummary[]> {
  const res = await authedFetch('/api/topics', { method: 'GET' });
  if (!res.ok) throw new Error(`Failed to fetch topics: ${res.status}`);
  const j = (await res.json()) as { items: TopicSummary[] };
  return j.items;
}

export async function fetchTopic(slug: string): Promise<TopicDetail> {
  const res = await authedFetch(
    `/api/topics/${encodeURIComponent(slug)}`,
    { method: 'GET' },
  );
  if (!res.ok) throw new Error(`Failed to fetch topic: ${res.status}`);
  return (await res.json()) as TopicDetail;
}

export async function deleteTopic(topicId: number): Promise<void> {
  const res = await authedFetch(`/api/topics/${topicId}`, { method: 'DELETE' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to delete topic: ${res.status} ${text}`);
  }
}

// --------------- Auto-tag jobs ---------------

export interface AutoTagJobError {
  batch_index: number;
  paper_ids: number[];
  message: string | null;
}

export interface AutoTagJobSnapshot {
  job_id: string;
  status: 'running' | 'done' | 'cancelled' | 'failed';
  total_papers: number;
  processed_papers: number;
  total_batches: number;
  processed_batches: number;
  succeeded_batches: number;
  failed_batches: number;
  cancelled_batches: number;
  batch_size: number;
  current_batch: { index: number; paper_ids: number[] } | null;
  errors: AutoTagJobError[];
  topic_id: number | null;
  topic_slug: string | null;
  topic_name: string | null;
  topic_summary: string | null;
  model: string | null;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
  last_error: string | null;
}

export async function startAutoTagJob(
  paperIds: number[],
): Promise<AutoTagJobSnapshot> {
  const body: Record<string, unknown> = { paper_ids: paperIds };
  const res = await authedFetch('/api/tags/auto', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to start auto-tag: ${res.status} ${text}`);
  }
  return (await res.json()) as AutoTagJobSnapshot;
}

export async function fetchAutoTagJob(
  jobId: string,
): Promise<AutoTagJobSnapshot> {
  const res = await authedFetch(
    `/api/tags/auto/${encodeURIComponent(jobId)}`,
    { method: 'GET' },
  );
  if (!res.ok) throw new Error(`Failed to poll auto-tag job: ${res.status}`);
  return (await res.json()) as AutoTagJobSnapshot;
}

export async function cancelAutoTagJob(jobId: string): Promise<void> {
  const res = await authedFetch(
    `/api/tags/auto/${encodeURIComponent(jobId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to cancel auto-tag job: ${res.status} ${text}`);
  }
}

export async function retryAutoTagJob(
  jobId: string,
): Promise<AutoTagJobSnapshot> {
  const res = await authedFetch(
    `/api/tags/auto/${encodeURIComponent(jobId)}/retry`,
    { method: 'POST' },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to retry auto-tag job: ${res.status} ${text}`);
  }
  return (await res.json()) as AutoTagJobSnapshot;
}

// --------------- Memory Ledger (events) API ---------------

export interface EventItem {
  id: number;
  ts: string;
  actor: 'user' | 'agent' | 'llm' | 'system';
  event_type: string;
  subject_type: 'paper' | 'tag' | 'topic';
  subject_id: string;
  related_ids: string[] | null;
  payload: Record<string, unknown> | null;
  schema_v: number;
}

export interface EventsListResponse {
  items: EventItem[];
  next_cursor: number | null;
}

export interface TimelineResponse {
  paper_id: number;
  arxiv_id: string | null;
  events: EventItem[];
}

export interface FetchEventsParams {
  subject_type?: 'paper' | 'tag' | 'topic';
  subject_id?: string;
  event_type?: string;
  actor?: 'user' | 'agent' | 'llm' | 'system';
  since?: string;
  limit?: number;
  cursor?: number;
}

export async function fetchEvents(
  params: FetchEventsParams = {},
): Promise<EventsListResponse> {
  const qs = new URLSearchParams();
  if (params.subject_type) qs.set('subject_type', params.subject_type);
  if (params.subject_id) qs.set('subject_id', params.subject_id);
  if (params.event_type) qs.set('event_type', params.event_type);
  if (params.actor) qs.set('actor', params.actor);
  if (params.since) qs.set('since', params.since);
  if (params.limit != null) qs.set('limit', String(params.limit));
  if (params.cursor != null) qs.set('cursor', String(params.cursor));
  const res = await authedFetch(`/api/events?${qs.toString()}`, { method: 'GET' });
  if (!res.ok) {
    throw new Error(`Failed to fetch events: ${res.status}`);
  }
  return (await res.json()) as EventsListResponse;
}

export async function fetchPaperTimeline(
  paperId: number,
): Promise<TimelineResponse> {
  const res = await authedFetch(`/api/papers/${paperId}/timeline`, {
    method: 'GET',
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch timeline: ${res.status}`);
  }
  return (await res.json()) as TimelineResponse;
}

// ---------- Navigator / Map ----------

/** A single point on the 2D embedding map. */
export interface MapPoint {
  id?: number;
  arxiv_id: string;
  x: number;
  y: number;
  title?: string;
}

/** A single event in the trajectory line. */
export interface TrajectorySegment {
  arxiv_id: string;
  ts: string;
  event_type: string;
}

/** A candidate paper returned by the arXiv feed. */
export interface FeedHit {
  arxiv_id: string;
  x: number;
  y: number;
  title?: string;
  abstract?: string;
  score?: number;
}

/** A blind-spot recommendation. */
export interface BlindSpot {
  arxiv_id: string;
  x: number;
  y: number;
  title?: string;
  abstract?: string;
  score?: number;
}

/** Response shape for GET /api/map. */
export interface MapData {
  library: MapPoint[];
  trajectory: TrajectorySegment[];
  feed_hits: FeedHit[];
  blind_spots: BlindSpot[];
}

/** Fetch the 2D embedding map. */
export async function fetchMapData(): Promise<MapData> {
  const res = await authedFetch('/api/map', { method: 'GET' });
  if (!res.ok) {
    throw new Error(`Failed to fetch map: ${res.status}`);
  }
  return (await res.json()) as MapData;
}

/** Ingest a feed paper by arXiv ID (Atlas "Add to Library"). */
export async function ingestFromFeed(arxivId: string): Promise<UploadIngestResponse> {
  const res = await authedFetch('/api/ingest/feed', {
    method: 'POST',
    body: JSON.stringify({ arxiv_id: arxivId }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to ingest feed paper: ${res.status} ${text}`);
  }
  return (await res.json()) as UploadIngestResponse;
}

// ---------- Weekly Digest ----------

export interface WeeklyDigest {
  id: number;
  week: string;
  week_start: string;
  content: string;
  user_note: string;
  created_at: string;
  updated_at: string;
}

/** Fetch the most recent weekly digests. */
export async function fetchWeeklyDigests(limit = 8): Promise<WeeklyDigest[]> {
  const res = await authedFetch(`/api/weekly-digests?limit=${limit}`, { method: 'GET' });
  if (!res.ok) {
    throw new Error(`Failed to fetch weekly digests: ${res.status}`);
  }
  return (await res.json()) as WeeklyDigest[];
}

/** Update user_note on a weekly digest. */
export async function updateDigestNote(digestId: number, userNote: string): Promise<void> {
  const res = await authedFetch(`/api/weekly-digests/${digestId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_note: userNote }),
  });
  if (!res.ok) {
    throw new Error(`Failed to update digest: ${res.status}`);
  }
}
