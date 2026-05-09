import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDialog } from '@/lib/dialog';
import { Icon } from '@/lib/icons';
import {
  deletePaper,
  editPaperTags,
  fetchDimensionValues,
  fetchFeedStatus,
  fetchPapers,
  openPaperPdf,
  pingAgent,
  uploadPdfToAgent,
  type DimensionValues,
  type FetchPapersParams,
  type PaperItem,
  type PaperTag,
  type UploadIngestResponse,
} from '@/lib/agent';
import { AutoTagPanel } from './AutoTagPanel';
import { navigate, useHashRoute } from './router';
import { TopicDetailView, TopicsView } from './TopicsView';
import { WeeklySidebar } from './WeeklySidebar';

const PAGE_SIZE = 20;
const POLL_INTERVAL_MS = 5_000;
type SortField = 'title' | 'ingested_at';
type SortOrder = 'asc' | 'desc';

/* =============================================================== *
 * Router shell
 * =============================================================== */

export default function App() {
  const route = useHashRoute();
  const [agentOk, setAgentOk] = useState<boolean | null>(null);

  useEffect(() => { pingAgent().then(setAgentOk); }, []);

  return (
    <div className="db-root">
      <Header route={route} agentOk={agentOk} />
      {route.name === 'papers' && (
        <div className="db-papers-layout">
          <div className="db-papers-main"><PapersPane /></div>
          <WeeklySidebar />
        </div>
      )}
      {route.name === 'topics' && (
        <div className="db-papers-layout">
          <div className="db-papers-main"><TopicsView /></div>
          <WeeklySidebar />
        </div>
      )}
      {route.name === 'topic' && (
        <div className="db-papers-layout">
          <div className="db-papers-main"><TopicDetailView slug={route.slug} /></div>
          <WeeklySidebar />
        </div>
      )}
    </div>
  );
}

function Header({
  route, agentOk,
}: {
  route: ReturnType<typeof useHashRoute>;
  agentOk: boolean | null;
}) {
  const isPapers = route.name === 'papers';
  const isTopics = route.name === 'topics' || route.name === 'topic';

  function openSettings() {
    // Prefer the canonical extension API; fall back to opening the
    // bundled options page in a new tab if it's unavailable (e.g. when
    // the dashboard is viewed outside an extension context).
    const runtime = (globalThis as any).chrome?.runtime;
    if (runtime?.openOptionsPage) {
      runtime.openOptionsPage();
      return;
    }
    if (runtime?.getURL) {
      window.open(runtime.getURL('options.html'), '_blank', 'noopener,noreferrer');
    }
  }

  return (
    <header className="db-header">
      <div className="db-hero-brand">
        <img className="db-hero-logo" src="/icon/128.png" alt="PaperPrism" />
        <div className="db-hero-text">
          <h1 className="db-title">
            Paper<span className="db-title-prism">Prism</span>
          </h1>
          <div className="db-hero-subtitle">Local Atlas · 127.0.0.1</div>
        </div>
      </div>
      <nav className="db-nav">
        <a
          href="#/"
          className={`db-nav-link ${isPapers ? 'active' : ''}`}
          onClick={(e) => { e.preventDefault(); navigate('#/'); }}
        >
          Papers
        </a>
        <a
          href="#/topics"
          className={`db-nav-link ${isTopics ? 'active' : ''}`}
          onClick={(e) => { e.preventDefault(); navigate('#/topics'); }}
        >
          Topics
        </a>
        <a
          href={chrome.runtime.getURL('map.html')}
          className="db-nav-link"
        >
          Atlas
        </a>
      </nav>
      <button
        type="button"
        className="pp-btn db-header-settings"
        data-variant="ghost"
        data-size="sm"
        onClick={openSettings}
        title="Open extension settings"
        aria-label="Open settings"
      >
        <Icon name="settings" />
        <span className="db-settings-label">Settings</span>
      </button>
      <span className={`db-badge ${agentOk === true ? 'ok' : agentOk === false ? 'err' : ''}`}>
        Agent: {agentOk === true ? 'online' : agentOk === false ? 'offline' : '...'}
      </span>
    </header>
  );
}

/* =============================================================== *
 * Papers pane
 * =============================================================== */

interface ImportError { name: string; message: string; }
interface ImportState {
  open: boolean; running: boolean;
  total: number; processed: number;
  succeeded: number; duplicate: number; failed: number;
  currentName: string; errors: ImportError[];
  aborted: boolean; finished: boolean;
}
const INITIAL_IMPORT_STATE: ImportState = {
  open: false, running: false,
  total: 0, processed: 0,
  succeeded: 0, duplicate: 0, failed: 0,
  currentName: '', errors: [],
  aborted: false, finished: false,
};

function PapersPane() {
  const { dialogNode, showAlert, showConfirm } = useDialog();
  const [items, setItems] = useState<PaperItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [sortField, setSortField] = useState<SortField>('ingested_at');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  const [filterTag, setFilterTag] = useState('');
  const [, setDimValues] = useState<DimensionValues>({});
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [pdfLoadingId, setPdfLoadingId] = useState<number | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const [importState, setImportState] = useState<ImportState>(INITIAL_IMPORT_STATE);
  const importAbortRef = useRef<AbortController | null>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [autoTagOpen, setAutoTagOpen] = useState(false);

  // Feed-ready toast: show once per day when today's arXiv feed is available.
  // Uses chrome.storage.local to persist "already shown today" across page reloads.
  const [feedToast, setFeedToast] = useState<{ count: number } | null>(null);

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10); // 'YYYY-MM-DD'
    const storageKey = 'feedPromptDate';

    const checkAndMaybePrompt = async () => {
      try {
        // Only prompt if we haven't already shown it today
        const stored = await chrome.storage.local.get(storageKey);
        if (stored[storageKey] === today) return;

        const status = await fetchFeedStatus();
        if (!status.ready) return;

        // Mark as shown for today before displaying, so a reload won't re-show
        await chrome.storage.local.set({ [storageKey]: today });
        setFeedToast({ count: status.count });
      } catch {
        // Agent offline or storage unavailable — silently skip
      }
    };

    void checkAndMaybePrompt();
  }, []);

  // Debounce search
  useEffect(() => {
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  useEffect(() => { fetchDimensionValues().then(setDimValues).catch(() => {}); }, []);

  const loadPapers = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = opts?.silent ?? false;
    if (!silent) setLoading(true);
    try {
      const params: FetchPapersParams = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort: sortField,
        order: sortOrder,
      };
      if (debouncedQuery) params.q = debouncedQuery;
      if (filterTag) params.tag = filterTag;
      const res = await fetchPapers(params);
      setItems(res.items);
      setTotal(res.total);
    } catch {
      if (!silent) { setItems([]); setTotal(0); }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [page, debouncedQuery, sortField, sortOrder, filterTag]);

  useEffect(() => { loadPapers(); }, [loadPapers]);

  useEffect(() => { setPage(0); }, [debouncedQuery, filterTag]);

  // Background polling (tab visible + no import / auto-tag modal open + no active load).
  const loadingRef = useRef(false);
  useEffect(() => { loadingRef.current = loading; }, [loading]);
  const importRunningRef = useRef(false);
  useEffect(() => { importRunningRef.current = importState.running; }, [importState.running]);
  const autoTagOpenRef = useRef(false);
  useEffect(() => { autoTagOpenRef.current = autoTagOpen; }, [autoTagOpen]);

  useEffect(() => {
    const tick = () => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      if (importRunningRef.current || autoTagOpenRef.current || loadingRef.current) return;
      loadPapers({ silent: true });
    };
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    const onVis = () => { if (document.visibilityState === 'visible') tick(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [loadPapers]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function toggleSort(field: SortField) {
    if (sortField === field) setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    else { setSortField(field); setSortOrder('desc'); }
  }
  function sortIndicator(field: SortField) {
    if (sortField !== field) return ' \u2195';
    return sortOrder === 'asc' ? ' \u2191' : ' \u2193';
  }

  /* --- selection --- */
  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  const selectAllOnPage = useCallback(() => {
    setSelectedIds((prev) => {
      const pageIds = items.map((p) => p.id);
      const allSelected = pageIds.every((pid) => prev.has(pid));
      const next = new Set(prev);
      if (allSelected) pageIds.forEach((pid) => next.delete(pid));
      else pageIds.forEach((pid) => next.add(pid));
      return next;
    });
  }, [items]);
  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const pageAllSelected = items.length > 0 && items.every((p) => selectedIds.has(p.id));

  /* --- delete --- */
  const handleDelete = useCallback(async (paper: PaperItem) => {
    const ok = await showConfirm(
      `Delete paper?`,
      `"${paper.title ?? paper.full_id}"\n\nThis will remove the database row and the vault files. This action cannot be undone.`,
      'Delete',
      'danger',
    );
    if (!ok) return;
    setDeletingId(paper.id);
    try {
      const res = await deletePaper(paper.id, true);
      if (res.deleted) {
        setItems((prev) => prev.filter((x) => x.id !== paper.id));
        setTotal((t) => Math.max(0, t - 1));
        setSelectedIds((prev) => {
          if (!prev.has(paper.id)) return prev;
          const next = new Set(prev); next.delete(paper.id); return next;
        });
      }
    } catch (err) {
      await showAlert('Delete failed', (err as Error).message);
    } finally {
      setDeletingId(null);
    }
  }, [showConfirm, showAlert]);

  const handleOpenPdf = useCallback(async (paper: PaperItem) => {
    setPdfLoadingId(paper.id);
    try { await openPaperPdf(paper.id, paper.arxiv_id ?? paper.full_id); }
    catch (err) { await showAlert('Open PDF failed', (err as Error).message); }
    finally { setPdfLoadingId(null); }
  }, [showAlert]);

  /* --- tag editor --- */
  const handleTagEdit = useCallback(
    async (paperId: number, payload: { add?: string[]; remove?: string[] }) => {
      const res = await editPaperTags(paperId, payload);
      setItems((prev) =>
        prev.map((p) => (p.id === paperId ? { ...p, tags: res.tags } : p)),
      );
    },
    [],
  );

  /* --- import --- */
  function pickFolder() {
    if (folderInputRef.current) folderInputRef.current.value = '';
    folderInputRef.current?.click();
  }
  const runImport = useCallback(async (files: File[]) => {
    const abort = new AbortController();
    importAbortRef.current = abort;
    setImportState({ ...INITIAL_IMPORT_STATE, open: true, running: true, total: files.length });
    let succeeded = 0, duplicate = 0, failed = 0;
    const errors: ImportError[] = [];
    for (let i = 0; i < files.length; i++) {
      if (abort.signal.aborted) break;
      const f = files[i];
      setImportState((s) => ({ ...s, currentName: f.name, processed: i }));
      try {
        const res: UploadIngestResponse = await uploadPdfToAgent(f, {
          sourceHint: (f as any).webkitRelativePath || f.name,
          signal: abort.signal,
        });
        if (res.duplicate) duplicate += 1;
        else if (res.accepted) succeeded += 1;
        else { failed += 1; errors.push({ name: f.name, message: res.message ?? 'rejected' }); }
      } catch (err) {
        if ((err as Error).name === 'AbortError') break;
        failed += 1;
        errors.push({ name: f.name, message: (err as Error).message });
      }
      setImportState((s) => ({ ...s, processed: i + 1, succeeded, duplicate, failed, errors }));
    }
    const aborted = abort.signal.aborted;
    setImportState((s) => ({ ...s, running: false, finished: true, aborted, currentName: '' }));
    importAbortRef.current = null;
    loadPapers().catch(() => {});
  }, [loadPapers]);

  function onFolderPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const all = Array.from(e.target.files ?? []);
    const pdfs = all.filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfs.length === 0) { void showAlert('No PDF files found', 'The selected folder contains no PDF files.'); return; }
    runImport(pdfs);
  }
  function cancelImport() { importAbortRef.current?.abort(); }
  function closeImportPanel() { if (!importState.running) setImportState(INITIAL_IMPORT_STATE); }

  /* --- auto-tag modal --- */
  const selectedIdsArray = useMemo(() => Array.from(selectedIds), [selectedIds]);
  const titleMap = useMemo(() => {
    const m = new Map<number, string>();
    items.forEach((p) => m.set(p.id, p.title ?? p.full_id));
    return m;
  }, [items]);

  return (
    <>
      {dialogNode}

      {feedToast && (
        <div className="feed-toast" role="alert">
          <div className="feed-toast-body">
            <span className="feed-toast-icon">✦</span>
            <div className="feed-toast-text">
              <strong>Today&apos;s stars are ready</strong>
              <span>{feedToast.count} new papers in the Atlas</span>
            </div>
            <button
              type="button"
              className="feed-toast-cta"
              onClick={() => { window.location.href = chrome.runtime.getURL('map.html'); }}
            >
              Open Atlas
            </button>
            <button
              type="button"
              className="feed-toast-close"
              aria-label="Dismiss"
              onClick={() => setFeedToast(null)}
            >
              ×
            </button>
          </div>
        </div>
      )}

      <div className="db-toolbar">
        <input
          className="db-search"
          type="text"
          placeholder="Search title, abstract, tag..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {filterTag && (
          <span className="db-filter-chip">
            <Icon name="filter" size={11} aria-hidden />
            tag: {filterTag}
            <button
              type="button"
              className="pp-btn db-filter-chip-clear"
              data-variant="subtle"
              data-size="sm"
              data-shape="round"
              onClick={() => setFilterTag('')}
              aria-label="Clear tag filter"
            >
              <Icon name="x" size={12} />
            </button>
          </span>
        )}
        <button
          type="button"
          className="pp-btn"
          data-variant="primary"
          data-size="md"
          onClick={pickFolder}
          disabled={importState.running}
          title="Import all PDFs from a local folder"
        >
          <Icon name="folder-plus" />
          {importState.running ? 'Importing...' : 'Import folder'}
        </button>
        <input
          ref={folderInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={onFolderPicked}
          {...({ webkitdirectory: '', directory: '' } as any)}
        />
      </div>

      {selectedIds.size > 0 && (
        <div className="db-bulkbar">
          <span className="db-bulkbar-info">
            {selectedIds.size} paper{selectedIds.size === 1 ? '' : 's'} selected
          </span>
          <button
            type="button"
            className="pp-btn"
            data-variant="accent"
            data-size="sm"
            onClick={() => setAutoTagOpen(true)}
            title="Cluster selected papers into a new topic"
          >
            <Icon name="layers" />
            Create topic
          </button>
          <button
            type="button"
            className="pp-btn"
            data-variant="ghost"
            data-size="sm"
            onClick={clearSelection}
            aria-label="Clear selection"
          >
            <Icon name="x" />
            Clear
          </button>
        </div>
      )}

      <div className="db-table-wrap">
        <table className="db-table">
          {/*
           * Explicit column widths via <colgroup> are MANDATORY here.
           * The table uses `table-layout: fixed`, which means the browser
           * decides each column's width from the first row.  Without
           * <colgroup>, Chrome computes thead and tbody column widths
           * independently and they end up cyclically shifted by one
           * column — the dashboard's classic "header / body misalignment"
           * bug.  Verified live with chrome-devtools MCP on a 1440px
           * viewport (2026-05-08).  Do NOT remove this colgroup without
           * also dropping `table-layout: fixed` from `.db-table`.
           */}
          <colgroup>
            <col className="col-check" />
            <col className="col-title" />
            <col className="col-abstract" />
            <col className="col-tags" />
            <col className="col-actions" />
          </colgroup>
          <thead>
            <tr>
              <th className="db-th db-th-check">
                <input
                  type="checkbox"
                  checked={pageAllSelected}
                  onChange={selectAllOnPage}
                  title="Select all on page"
                />
              </th>
              <th className="db-th sortable" onClick={() => toggleSort('title')}>Title{sortIndicator('title')}</th>
              <th className="db-th">Abstract</th>
              <th className="db-th">Tags</th>
              <th className="db-th actions-th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 && (
              <tr><td colSpan={5} className="db-empty">Loading...</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={5} className="db-empty">No papers found.</td></tr>
            )}
            {items.map((p) => (
              <PaperRow
                key={p.id}
                paper={p}
                selected={selectedIds.has(p.id)}
                onToggleSelect={toggleSelect}
                onDelete={handleDelete}
                onOpenPdf={handleOpenPdf}
                onEditTags={handleTagEdit}
                onFilterByTag={setFilterTag}
                deleting={deletingId === p.id}
                pdfLoading={pdfLoadingId === p.id}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="db-pagination">
        <button
          type="button"
          className="pp-btn"
          data-variant="ghost"
          data-size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          aria-label="Previous page"
        >
          <Icon name="chevron-left" />
          Prev
        </button>
        <span className="db-page-info">
          Page {page + 1} of {totalPages} ({total} papers)
        </span>
        <button
          type="button"
          className="pp-btn"
          data-variant="ghost"
          data-size="sm"
          disabled={page + 1 >= totalPages}
          onClick={() => setPage((p) => p + 1)}
          aria-label="Next page"
        >
          Next
          <Icon name="chevron-right" />
        </button>
      </div>

      {importState.open && (
        <ImportPanel state={importState} onCancel={cancelImport} onClose={closeImportPanel} />
      )}

      {autoTagOpen && (
        <AutoTagPanel
          paperIds={selectedIdsArray}
          paperTitles={titleMap}
          onClose={() => setAutoTagOpen(false)}
          onDone={() => {
            // Reload the current page so new tags surface on existing rows.
            loadPapers({ silent: true });
          }}
        />
      )}
    </>
  );
}

/* =============================================================== *
 * Import panel (unchanged from v0.2; kept here to avoid another file)
 * =============================================================== */

function ImportPanel({
  state, onCancel, onClose,
}: { state: ImportState; onCancel: () => void; onClose: () => void; }) {
  const pct = state.total > 0 ? Math.min(100, Math.round((state.processed / state.total) * 100)) : 0;
  const showErrors = state.errors.slice(-5);
  return (
    <div className="db-import-backdrop" role="dialog" aria-modal="true">
      <div className="db-import-panel">
        <div className="db-import-head">
          <h2 className="db-import-title">Import folder</h2>
          {!state.running && (
            <button type="button" className="db-import-x" onClick={onClose} aria-label="Close">×</button>
          )}
        </div>
        <div className="db-import-progressbar">
          <div className="db-import-progressbar-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="db-import-progress-label">
          {state.running
            ? `${state.processed} / ${state.total} (${pct}%)`
            : state.aborted
              ? `Cancelled after ${state.processed} of ${state.total}`
              : `Done — processed ${state.processed} of ${state.total}`}
        </div>
        {state.running && state.currentName && (
          <div className="db-import-current" title={state.currentName}>
            Current: {state.currentName}
          </div>
        )}
        <div className="db-import-stats">
          <span className="db-import-stat db-import-stat-ok">Imported: {state.succeeded}</span>
          <span className="db-import-stat db-import-stat-dup">Duplicates: {state.duplicate}</span>
          <span className="db-import-stat db-import-stat-err">Failed: {state.failed}</span>
        </div>
        {showErrors.length > 0 && (
          <div className="db-import-errors">
            <div className="db-import-errors-title">Last errors</div>
            <ul className="db-import-errors-list">
              {showErrors.map((e, idx) => (
                <li key={`${e.name}-${idx}`}>
                  <span className="db-import-err-name">{e.name}</span>
                  <span className="db-import-err-msg">: {e.message}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="db-import-actions">
          {state.running ? (
            <button type="button" className="db-import-cancel" onClick={onCancel}>Cancel</button>
          ) : (
            <button type="button" className="db-import-close" onClick={onClose}>Close</button>
          )}
        </div>
      </div>
    </div>
  );
}

/* =============================================================== *
 * Paper row (checkbox + tag editor in expand area)
 * =============================================================== */

function PaperRow({
  paper, selected,
  onToggleSelect, onDelete, onOpenPdf, onEditTags, onFilterByTag,
  deleting, pdfLoading,
}: {
  paper: PaperItem;
  selected: boolean;
  onToggleSelect: (id: number) => void;
  onDelete: (p: PaperItem) => void;
  onOpenPdf: (p: PaperItem) => void;
  onEditTags: (paperId: number, payload: { add?: string[]; remove?: string[] }) => Promise<void>;
  onFilterByTag: (name: string) => void;
  deleting: boolean;
  pdfLoading: boolean;
}) {
  const titleText = paper.title ?? paper.full_id;
  // Prefer LLM-generated summary; fallback to raw abstract
  const summaryCls = (paper.classifications?.summary ?? []);
  const displayText = summaryCls.length > 0 ? summaryCls.join(' ') : (paper.abstract ?? '');
  const isLLMSummary = summaryCls.length > 0;

  return (
    <tr className={`db-row ${selected ? 'selected' : ''}`}>
      <td className="db-td db-td-check" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(paper.id)}
          aria-label="Select paper"
        />
      </td>
      <td className="db-td title-cell" title={titleText}>
        <div className="db-title-text">{titleText}</div>
        <span className="db-arxiv-id">{paper.full_id}</span>
        <div className="db-expand-meta">
          {paper.abs_url && (
            <a
              href={paper.abs_url}
              target="_blank"
              rel="noreferrer"
              className="db-link"
              onClick={(e) => e.stopPropagation()}
              title="Open arxiv abstract page"
            >
              <Icon name="external-link" size={11} aria-hidden />
              arxiv
            </a>
          )}
          {paper.code_url && (
            <a
              href={paper.code_url}
              target="_blank"
              rel="noreferrer"
              className="db-link"
              onClick={(e) => e.stopPropagation()}
              title="Open code repository"
            >
              <Icon name="external-link" size={11} aria-hidden />
              code
            </a>
          )}
        </div>
      </td>
      <td className="db-td abstract-cell" title={displayText}>
        <div className={`db-abstract-inline ${isLLMSummary ? 'db-abstract-llm' : ''}`}>
          {displayText || 'No abstract available.'}
        </div>
      </td>
      <td className="db-td tags-cell" onClick={(e) => e.stopPropagation()}>
        <InlineTagEditor paper={paper} onEditTags={onEditTags} onFilterByTag={onFilterByTag} />
      </td>
      <td className="db-td actions-cell">
        <div className="pp-btn-group">
          <button
            type="button"
            className="pp-btn"
            data-variant="ghost"
            data-size="sm"
            data-shape="square"
            disabled={pdfLoading}
            onClick={() => onOpenPdf(paper)}
            title="Preview PDF in new tab"
            aria-label={pdfLoading ? 'Opening preview' : 'Preview PDF'}
          >
            {pdfLoading
              ? <span className="pp-btn-spinner" aria-hidden />
              : <Icon name="eye" />}
          </button>
          <button
            type="button"
            className="pp-btn"
            data-variant="danger"
            data-size="sm"
            data-shape="square"
            disabled={deleting}
            onClick={() => onDelete(paper)}
            title="Delete paper (DB row + vault files)"
            aria-label={deleting ? 'Deleting paper' : 'Delete paper'}
          >
            {deleting
              ? <span className="pp-btn-spinner" aria-hidden />
              : <Icon name="trash" />}
          </button>
        </div>
      </td>
    </tr>
  );
}

/* =============================================================== *
 * Inline tag editor (used directly in the Tags cell)
 * =============================================================== */

function InlineTagEditor({
  paper, onEditTags, onFilterByTag,
}: {
  paper: PaperItem;
  onEditTags: (paperId: number, payload: { add?: string[]; remove?: string[] }) => Promise<void>;
  onFilterByTag: (name: string) => void;
}) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const commitAdd = useCallback(async () => {
    const raw = input.trim();
    if (!raw) return;
    const tags = raw.split(/[,\n]/).map((t) => t.trim()).filter(Boolean);
    if (tags.length === 0) return;
    setBusy(true);
    try {
      await onEditTags(paper.id, { add: tags });
      setInput('');
    } finally {
      setBusy(false);
    }
  }, [input, paper.id, onEditTags]);

  const removeOne = useCallback(async (tag: PaperTag) => {
    setBusy(true);
    try {
      await onEditTags(paper.id, { remove: [tag.name] });
    } finally {
      setBusy(false);
    }
  }, [paper.id, onEditTags]);

  return (
    <div className="db-inline-tag-editor">
      <div className="db-inline-tag-chips">
        {paper.tags.length === 0 && <span className="db-tags-empty">{'\u2014'}</span>}
        {paper.tags.map((t) => (
          <span
            key={t.id}
            className={`db-tag-chip ${t.source === 'user' ? 'db-tag-chip-user' : 'db-tag-chip-llm'}`}
            title={`${t.source} tag${t.source === 'llm' ? '' : ' (user)'} – click × to remove`}
          >
            <span
              className="db-tag-chip-name"
              onClick={() => onFilterByTag(t.name)}
            >
              {t.name}
            </span>
            <button
              type="button"
              className="db-tag-chip-x"
              onClick={() => removeOne(t)}
              disabled={busy}
              aria-label={`Remove tag ${t.name}`}
            >
              <Icon name="x" size={10} />
            </button>
          </span>
        ))}
      </div>
      <form
        className="db-inline-tag-form"
        onSubmit={(e) => { e.preventDefault(); commitAdd(); }}
      >
        <input
          type="text"
          placeholder="+ tag"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
        />
      </form>
    </div>
  );
}
