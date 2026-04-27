import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchPapers,
  fetchDimensionValues,
  deletePaper,
  openPaperPdf,
  pingAgent,
  type PaperItem,
  type FetchPapersParams,
  type DimensionValues,
} from '@/lib/agent';

const PAGE_SIZE = 20;
type SortField = 'ingested_at' | 'published_at' | 'title';
type SortOrder = 'asc' | 'desc';

export default function App() {
  const [items, setItems] = useState<PaperItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [sortField, setSortField] = useState<SortField>('ingested_at');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
  const [filterDomain, setFilterDomain] = useState('');
  const [filterAffiliation, setFilterAffiliation] = useState('');
  const [dimValues, setDimValues] = useState<DimensionValues>({});
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [agentOk, setAgentOk] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [pdfLoadingId, setPdfLoadingId] = useState<number | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Debounce search
  useEffect(() => {
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  // Probe agent
  useEffect(() => {
    pingAgent().then(setAgentOk);
  }, []);

  // Load dimension values
  useEffect(() => {
    fetchDimensionValues().then(setDimValues).catch(() => {});
  }, []);

  // Load papers whenever filters/sort/page change
  const loadPapers = useCallback(async () => {
    setLoading(true);
    try {
      const params: FetchPapersParams = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort: sortField,
        order: sortOrder,
      };
      if (debouncedQuery) params.q = debouncedQuery;
      if (filterDomain) params.domain = filterDomain;
      if (filterAffiliation) params.affiliations = filterAffiliation;
      const res = await fetchPapers(params);
      setItems(res.items);
      setTotal(res.total);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, debouncedQuery, sortField, sortOrder, filterDomain, filterAffiliation]);

  useEffect(() => {
    loadPapers();
  }, [loadPapers]);

  // Reset to first page when filters change
  useEffect(() => {
    setPage(0);
  }, [debouncedQuery, filterDomain, filterAffiliation]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function toggleSort(field: SortField) {
    if (sortField === field) {
      setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  }

  function sortIndicator(field: SortField) {
    if (sortField !== field) return ' \u2195';
    return sortOrder === 'asc' ? ' \u2191' : ' \u2193';
  }

  function fmtDate(iso: string | null) {
    if (!iso) return '\u2014';
    const d = new Date(iso);
    return d.toLocaleDateString('en-CA'); // YYYY-MM-DD
  }

  const handleDelete = useCallback(
    async (paper: PaperItem) => {
      const label = paper.title ?? paper.full_id;
      const ok = window.confirm(
        `Delete "${label}"?\n\nThis will remove the database row and the vault files. This action cannot be undone.`,
      );
      if (!ok) return;
      setDeletingId(paper.id);
      try {
        const res = await deletePaper(paper.id, true);
        if (res.deleted) {
          setItems((prev) => prev.filter((x) => x.id !== paper.id));
          setTotal((t) => Math.max(0, t - 1));
          if (expandedId === paper.id) setExpandedId(null);
        } else {
          window.alert('Paper was not deleted (not found).');
        }
      } catch (err) {
        window.alert(`Delete failed: ${(err as Error).message}`);
      } finally {
        setDeletingId(null);
      }
    },
    [expandedId],
  );

  const handleOpenPdf = useCallback(async (paper: PaperItem) => {
    setPdfLoadingId(paper.id);
    try {
      await openPaperPdf(paper.id);
    } catch (err) {
      window.alert(`Open PDF failed: ${(err as Error).message}`);
    } finally {
      setPdfLoadingId(null);
    }
  }, []);

  return (
    <div className="db-root">
      {/* Header */}
      <header className="db-header">
        <h1 className="db-title">PaperPrism</h1>
        <span className={`db-badge ${agentOk === true ? 'ok' : agentOk === false ? 'err' : ''}`}>
          Agent: {agentOk === true ? 'online' : agentOk === false ? 'offline' : '...'}
        </span>
      </header>

      {/* Toolbar */}
      <div className="db-toolbar">
        <input
          className="db-search"
          type="text"
          placeholder="Search title, abstract, venue..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="db-select"
          value={filterDomain}
          onChange={(e) => setFilterDomain(e.target.value)}
        >
          <option value="">All Domains</option>
          {(dimValues.domain ?? []).map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <select
          className="db-select"
          value={filterAffiliation}
          onChange={(e) => setFilterAffiliation(e.target.value)}
        >
          <option value="">All Affiliations</option>
          {(dimValues.affiliations ?? []).map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="db-table-wrap">
        <table className="db-table">
          <thead>
            <tr>
              <th className="db-th sortable" onClick={() => toggleSort('title')}>
                Title{sortIndicator('title')}
              </th>
              <th className="db-th">Authors</th>
              <th className="db-th">Domain</th>
              <th className="db-th">Affiliations</th>
              <th className="db-th">Venue</th>
              <th className="db-th sortable" onClick={() => toggleSort('published_at')}>
                Published{sortIndicator('published_at')}
              </th>
              <th className="db-th sortable" onClick={() => toggleSort('ingested_at')}>
                Ingested{sortIndicator('ingested_at')}
              </th>
              <th className="db-th actions-th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 && (
              <tr><td colSpan={8} className="db-empty">Loading...</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={8} className="db-empty">No papers found.</td></tr>
            )}
            {items.map((p) => (
              <PaperRow
                key={p.id}
                paper={p}
                expanded={expandedId === p.id}
                onToggle={() => setExpandedId(expandedId === p.id ? null : p.id)}
                onDelete={handleDelete}
                onOpenPdf={handleOpenPdf}
                deleting={deletingId === p.id}
                pdfLoading={pdfLoadingId === p.id}
                fmtDate={fmtDate}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="db-pagination">
        <button
          className="db-page-btn"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          Previous
        </button>
        <span className="db-page-info">
          Page {page + 1} of {totalPages} ({total} papers)
        </span>
        <button
          className="db-page-btn"
          disabled={page + 1 >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function PaperRow({
  paper,
  expanded,
  onToggle,
  onDelete,
  onOpenPdf,
  deleting,
  pdfLoading,
  fmtDate,
}: {
  paper: PaperItem;
  expanded: boolean;
  onToggle: () => void;
  onDelete: (p: PaperItem) => void;
  onOpenPdf: (p: PaperItem) => void;
  deleting: boolean;
  pdfLoading: boolean;
  fmtDate: (iso: string | null) => string;
}) {
  const cls = paper.classifications;
  const domains = cls.domain ?? [];
  const affiliations = cls.affiliations ?? [];

  return (
    <>
      <tr className={`db-row ${expanded ? 'expanded' : ''}`} onClick={onToggle}>
        <td className="db-td title-cell">
          <div className="db-title-text">{paper.title ?? paper.full_id}</div>
          <span className="db-arxiv-id">{paper.full_id}</span>
        </td>
        <td className="db-td">
          {paper.first_author ?? '\u2014'}
          {paper.authors.length > 1 && (
            <span className="db-et-al"> +{paper.authors.length - 1}</span>
          )}
        </td>
        <td className="db-td">
          {domains.map((d) => (
            <span key={d} className="db-badge-dim domain">{d}</span>
          ))}
        </td>
        <td className="db-td">
          {affiliations.map((a) => (
            <span key={a} className="db-badge-dim affiliation">{a}</span>
          ))}
        </td>
        <td className="db-td venue-cell">{paper.venue ?? '\u2014'}</td>
        <td className="db-td date-cell">{fmtDate(paper.published_at)}</td>
        <td className="db-td date-cell">{fmtDate(paper.ingested_at)}</td>
        <td className="db-td actions-cell" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            className="db-pdf-btn"
            disabled={pdfLoading}
            onClick={(e) => {
              e.stopPropagation();
              onOpenPdf(paper);
            }}
            title="Open PDF in new tab"
          >
            {pdfLoading ? '...' : 'PDF'}
          </button>
          <button
            type="button"
            className="db-delete-btn"
            disabled={deleting}
            onClick={(e) => {
              e.stopPropagation();
              onDelete(paper);
            }}
            title="Delete paper (DB row + vault files)"
          >
            {deleting ? '...' : 'Delete'}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="db-expand-row">
          <td colSpan={8}>
            <div className="db-abstract">
              {paper.abstract
                ? paper.abstract.length > 600
                  ? paper.abstract.slice(0, 600) + '...'
                  : paper.abstract
                : 'No abstract available.'}
            </div>
            <div className="db-expand-meta">
              <button
                type="button"
                className="db-link db-link-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenPdf(paper);
                }}
                disabled={pdfLoading}
              >
                {pdfLoading ? 'opening...' : 'Open PDF'}
              </button>
              {paper.abs_url && (
                <a href={paper.abs_url} target="_blank" rel="noreferrer" className="db-link">
                  arxiv
                </a>
              )}
              {paper.code_url && (
                <a href={paper.code_url} target="_blank" rel="noreferrer" className="db-link">
                  code
                </a>
              )}
              {paper.arxiv_categories.length > 0 && (
                <span className="db-cats">
                  {paper.arxiv_categories.join(', ')}
                </span>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
