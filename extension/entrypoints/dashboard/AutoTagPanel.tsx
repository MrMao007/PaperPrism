import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cancelAutoTagJob,
  fetchAutoTagJob,
  retryAutoTagJob,
  startAutoTagJob,
  type AutoTagJobSnapshot,
} from '@/lib/agent';
import { navigate } from './router';

const POLL_MS = 1500;

interface Props {
  paperIds: number[];
  paperTitles: Map<number, string>;
  onClose: () => void;
  onDone: (snapshot: AutoTagJobSnapshot) => void;
}

type Phase = 'confirm' | 'running' | 'done';

/**
 * Drives a single auto-tag job through its lifecycle:
 *   confirm (user reviews selection) -> running (polls progress)
 *   -> done (shows result summary + links).
 */
export function AutoTagPanel({ paperIds, paperTitles, onClose, onDone }: Props) {
  const [phase, setPhase] = useState<Phase>('confirm');
  const [snapshot, setSnapshot] = useState<AutoTagJobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pollTimer = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollTimer.current != null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  };

  const pollOnce = useCallback(async (jobId: string) => {
    try {
      const snap = await fetchAutoTagJob(jobId);
      setSnapshot(snap);
      if (snap.status === 'running') {
        pollTimer.current = window.setTimeout(() => pollOnce(jobId), POLL_MS);
      } else {
        setPhase('done');
        onDone(snap);
      }
    } catch (err) {
      setError((err as Error).message);
      pollTimer.current = window.setTimeout(() => pollOnce(jobId), POLL_MS * 2);
    }
  }, [onDone]);

  useEffect(() => () => stopPolling(), []);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const snap = await startAutoTagJob(paperIds);
      setSnapshot(snap);
      setPhase('running');
      pollTimer.current = window.setTimeout(() => pollOnce(snap.job_id), POLL_MS);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [paperIds, pollOnce]);

  const handleCancel = useCallback(async () => {
    if (!snapshot) return;
    setBusy(true);
    try {
      await cancelAutoTagJob(snapshot.job_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [snapshot]);

  const handleRetry = useCallback(async () => {
    if (!snapshot) return;
    setBusy(true);
    setError(null);
    try {
      const snap = await retryAutoTagJob(snapshot.job_id);
      setSnapshot(snap);
      setPhase('running');
      pollTimer.current = window.setTimeout(() => pollOnce(snap.job_id), POLL_MS);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [snapshot, pollOnce]);

  const handleClose = () => {
    if (phase === 'running') return;
    stopPolling();
    onClose();
  };

  return (
    <div className="db-import-backdrop" role="dialog" aria-modal="true">
      <div className="db-import-panel db-tag-panel">
        <div className="db-import-head">
          <h2 className="db-import-title">
            {phase === 'confirm' && `Create topic from ${paperIds.length} paper${paperIds.length === 1 ? '' : 's'}`}
            {phase === 'running' && 'Auto-tagging & creating topic'}
            {phase === 'done' && (snapshot?.status === 'done' ? 'Topic created' : 'Auto-tag finished')}
          </h2>
          {phase !== 'running' && (
            <button type="button" className="db-import-x" onClick={handleClose} aria-label="Close">
              ×
            </button>
          )}
        </div>

        {phase === 'confirm' && (
          <ConfirmView
            paperIds={paperIds}
            paperTitles={paperTitles}
            onStart={handleStart}
            onCancel={handleClose}
            busy={busy}
            error={error}
          />
        )}

        {(phase === 'running' || phase === 'done') && snapshot && (
          <ProgressView
            snap={snapshot}
            onCancel={handleCancel}
            onRetry={handleRetry}
            onClose={handleClose}
            onGoTopic={() => {
              if (snapshot.topic_slug) {
                stopPolling();
                onClose();
                navigate(`#/topics/${encodeURIComponent(snapshot.topic_slug)}`);
              }
            }}
            busy={busy}
            error={error}
          />
        )}
      </div>
    </div>
  );
}

function ConfirmView({
  paperIds, paperTitles, onStart, onCancel, busy, error,
}: {
  paperIds: number[];
  paperTitles: Map<number, string>;
  onStart: () => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
}) {
  const preview = paperIds.slice(0, 5);
  return (
    <>
      <div className="db-tag-preview">
        <div className="db-tag-preview-title">
          {paperIds.length} paper{paperIds.length === 1 ? '' : 's'} selected — a topic will be created from shared tags
        </div>
        <ul className="db-tag-preview-list">
          {preview.map((pid) => (
            <li key={pid} title={paperTitles.get(pid) ?? `#${pid}`}>
              {paperTitles.get(pid) ?? `paper #${pid}`}
            </li>
          ))}
          {paperIds.length > preview.length && (
            <li className="db-tag-preview-more">… and {paperIds.length - preview.length} more</li>
          )}
        </ul>
      </div>

      {error && <div className="db-tag-error">{error}</div>}

      <div className="db-import-actions">
        <button type="button" className="db-import-close" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="db-import-btn" onClick={onStart} disabled={busy || paperIds.length === 0}>
          {busy ? 'Starting…' : 'Create topic'}
        </button>
      </div>
    </>
  );
}

function ProgressView({
  snap, onCancel, onRetry, onClose, onGoTopic, busy, error,
}: {
  snap: AutoTagJobSnapshot;
  onCancel: () => void;
  onRetry: () => void;
  onClose: () => void;
  onGoTopic: () => void;
  busy: boolean;
  error: string | null;
}) {
  const pct =
    snap.total_papers > 0
      ? Math.min(100, Math.round((snap.processed_papers / snap.total_papers) * 100))
      : 0;
  const running = snap.status === 'running';
  const hasFailed = snap.failed_batches > 0;

  return (
    <>
      <div className="db-import-progressbar">
        <div className="db-import-progressbar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="db-import-progress-label">
        {running
          ? `${snap.processed_papers} / ${snap.total_papers} papers · batch ${snap.processed_batches}/${snap.total_batches}`
          : snap.status === 'done'
            ? `Done — ${snap.processed_papers} papers across ${snap.total_batches} batch${snap.total_batches === 1 ? '' : 'es'}`
            : snap.status === 'cancelled'
              ? `Cancelled at ${snap.processed_papers}/${snap.total_papers}`
              : `Failed — ${snap.last_error ?? 'unknown error'}`}
      </div>

      {running && snap.current_batch && (
        <div className="db-import-current">
          Batch {snap.current_batch.index + 1}: {snap.current_batch.paper_ids.length} papers
        </div>
      )}

      <div className="db-import-stats">
        <span className="db-import-stat db-import-stat-ok">
          OK batches: {snap.succeeded_batches}
        </span>
        {hasFailed && (
          <span className="db-import-stat db-import-stat-err">
            Failed: {snap.failed_batches}
          </span>
        )}
        {snap.cancelled_batches > 0 && (
          <span className="db-import-stat db-import-stat-dup">
            Cancelled: {snap.cancelled_batches}
          </span>
        )}
        {snap.model && <span className="db-import-stat">Model: {snap.model}</span>}
      </div>

      {snap.errors.length > 0 && (
        <div className="db-import-errors">
          <div className="db-import-errors-title">Batch errors</div>
          <ul className="db-import-errors-list">
            {snap.errors.slice(-5).map((e, idx) => (
              <li key={`${e.batch_index}-${idx}`}>
                <span className="db-import-err-name">Batch {e.batch_index + 1}</span>
                <span className="db-import-err-msg"> — {e.message ?? 'error'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {snap.status === 'done' && snap.topic_name && (
        <div className="db-tag-topic-card">
          <div className="db-tag-topic-name">{snap.topic_name}</div>
          {snap.topic_summary && <div className="db-tag-topic-sum">{snap.topic_summary}</div>}
        </div>
      )}

      {error && <div className="db-tag-error">{error}</div>}

      <div className="db-import-actions">
        {running ? (
          <button type="button" className="db-import-cancel" onClick={onCancel} disabled={busy}>
            {busy ? 'Cancelling…' : 'Cancel'}
          </button>
        ) : (
          <>
            {hasFailed && (
              <button type="button" className="db-import-close" onClick={onRetry} disabled={busy}>
                Retry failed
              </button>
            )}
            {snap.status === 'done' && snap.topic_slug && (
              <button type="button" className="db-import-btn" onClick={onGoTopic}>
                View topic
              </button>
            )}
            <button type="button" className="db-import-close" onClick={onClose}>
              Close
            </button>
          </>
        )}
      </div>
    </>
  );
}
