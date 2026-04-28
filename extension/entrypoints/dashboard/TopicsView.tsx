import { useCallback, useEffect, useState } from 'react';
import {
  deleteTopic,
  fetchTopic,
  fetchTopics,
  openPaperPdf,
  type PaperItem,
  type TopicDetail,
  type TopicSummary,
} from '@/lib/agent';
import { navigate } from './router';

export function TopicsView() {
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTopics(await fetchTopics());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onDelete = useCallback(async (topic: TopicSummary) => {
    if (!window.confirm(`Delete topic "${topic.name}"?\n\nPapers and their tags remain; only the topic aggregation is removed.`)) return;
    try {
      await deleteTopic(topic.id);
      setTopics((prev) => prev.filter((t) => t.id !== topic.id));
    } catch (err) {
      window.alert(`Delete failed: ${(err as Error).message}`);
    }
  }, []);

  return (
    <div className="db-topics-root">
      <div className="db-topics-head">
        <h2>Topics</h2>
        <span className="db-topics-count">{topics.length} topic{topics.length === 1 ? '' : 's'}</span>
      </div>

      {error && <div className="db-tag-error">{error}</div>}
      {loading && topics.length === 0 && <div className="db-empty">Loading…</div>}
      {!loading && topics.length === 0 && !error && (
        <div className="db-empty">
          No topics yet. Select papers in the Papers view and use Auto-tag.
        </div>
      )}

      <div className="db-topics-grid">
        {topics.map((t) => (
          <div key={t.id} className="db-topic-card">
            <button
              type="button"
              className="db-topic-card-title"
              onClick={() => navigate(`#/topics/${encodeURIComponent(t.slug)}`)}
            >
              {t.name}
            </button>
            <div className="db-topic-card-meta">
              {t.paper_count} paper{t.paper_count === 1 ? '' : 's'} · {new Date(t.created_at).toLocaleDateString('en-CA')}
            </div>
            {t.summary && <div className="db-topic-card-sum">{t.summary}</div>}
            <div className="db-topic-card-tags">
              {t.top_tags.map((tag) => (
                <span key={tag} className="db-tag-chip db-tag-chip-llm">{tag}</span>
              ))}
            </div>
            <div className="db-topic-card-actions">
              <button
                type="button"
                className="db-delete-btn"
                onClick={() => onDelete(t)}
                title="Delete topic (papers & tags remain)"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}


export function TopicDetailView({ slug }: { slug: string }) {
  const [topic, setTopic] = useState<TopicDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterTag, setFilterTag] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTopic(await fetchTopic(slug));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  const onOpenPdf = useCallback(async (paper: PaperItem) => {
    try { await openPaperPdf(paper.id); }
    catch (err) { window.alert(`Open PDF failed: ${(err as Error).message}`); }
  }, []);

  if (loading) return <div className="db-empty">Loading…</div>;
  if (error || !topic) {
    return (
      <div className="db-topic-detail">
        <button type="button" className="db-link db-link-btn" onClick={() => navigate('#/topics')}>
          ← All topics
        </button>
        <div className="db-tag-error">{error ?? 'Topic not found'}</div>
      </div>
    );
  }

  const papers = filterTag
    ? topic.papers.filter((p) => p.tags.some((t) => t.name === filterTag))
    : topic.papers;

  return (
    <div className="db-topic-detail">
      <button type="button" className="db-link db-link-btn" onClick={() => navigate('#/topics')}>
        ← All topics
      </button>
      <h2 className="db-topic-title">{topic.name}</h2>
      <div className="db-topic-meta">
        {topic.paper_count} papers · created {new Date(topic.created_at).toLocaleString('en-CA')}
        {topic.model ? ` · ${topic.model}` : ''}
      </div>
      {topic.summary && <p className="db-topic-summary">{topic.summary}</p>}

      {topic.top_tags.length > 0 && (
        <div className="db-topic-tagbar">
          <button
            type="button"
            className={`db-tag-chip ${filterTag === '' ? 'db-tag-chip-active' : ''}`}
            onClick={() => setFilterTag('')}
          >
            All
          </button>
          {topic.top_tags.map((t) => (
            <button
              key={t}
              type="button"
              className={`db-tag-chip db-tag-chip-llm ${filterTag === t ? 'db-tag-chip-active' : ''}`}
              onClick={() => setFilterTag(filterTag === t ? '' : t)}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      <div className="db-topic-papers">
        {papers.map((p) => (
          <div key={p.id} className="db-topic-paper">
            <div className="db-topic-paper-row">
              <div className="db-topic-paper-title" title={p.title ?? p.full_id}>
                {p.title ?? p.full_id}
              </div>
              <button type="button" className="db-pdf-btn" onClick={() => onOpenPdf(p)}>
                PDF
              </button>
            </div>
            <div className="db-topic-paper-meta">
              {p.first_author ?? '—'}
              {p.authors.length > 1 && <span className="db-et-al"> +{p.authors.length - 1}</span>}
              {p.venue ? ` · ${p.venue}` : ''}
              {p.published_at ? ` · ${new Date(p.published_at).toLocaleDateString('en-CA')}` : ''}
            </div>
            <div className="db-topic-paper-tags">
              {p.tags.map((t) => (
                <span
                  key={t.id}
                  className={`db-tag-chip ${t.source === 'user' ? 'db-tag-chip-user' : 'db-tag-chip-llm'}`}
                >
                  {t.name}
                </span>
              ))}
            </div>
          </div>
        ))}
        {papers.length === 0 && <div className="db-empty">No papers match the selected tag.</div>}
      </div>
    </div>
  );
}
