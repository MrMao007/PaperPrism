import { useEffect, useState, useCallback } from 'react';
import { fetchMapData, fetchLlmConfig, saveLlmConfig, ingestFromFeed, type MapData } from '../../lib/agent';
import { groupedCategories, type ArxivCategory } from '../../lib/arxivCategories';
import { useDialog } from '../../lib/dialog';
import { Icon } from '../../lib/icons';
import CanvasMap from './CanvasMap';
import PointDrawer from './PointDrawer';

export default function MapApp() {
  const { dialogNode, showAlert } = useDialog();
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{
    kind: string;
    arxivId: string;
    title?: string;
    abstract?: string;
  } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedCats, setSelectedCats] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [ingesting, setIngesting] = useState(false);

  // Initial load + 5s polling. Pauses while the tab is hidden to avoid
  // burning CPU on the Agent (UMAP runs on every /api/map call); resumes
  // immediately on visibility change so users see fresh data the moment
  // they return to the Atlas tab.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async () => {
      try {
        const d = await fetchMapData();
        if (cancelled) return;
        setData(d);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError((err as Error).message || 'Failed to load map');
      }
    };

    const schedule = () => {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') return;
      timer = setTimeout(async () => {
        await refresh();
        schedule();
      }, 5000);
    };

    const onVisibility = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      if (document.visibilityState === 'visible') {
        void refresh().then(schedule);
      }
    };

    void refresh().then(schedule);
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  // Load current feed_categories from Agent
  const loadCategories = useCallback(async () => {
    try {
      const cfg = await fetchLlmConfig();
      setSelectedCats(cfg.feed_categories || []);
    } catch { /* agent offline — keep defaults */ }
  }, []);

  useEffect(() => { void loadCategories(); }, [loadCategories]);

  const toggleCat = useCallback((id: string) => {
    setSelectedCats((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
  }, []);

  const saveCategories = useCallback(async () => {
    setSaving(true);
    try {
      const cfg = await fetchLlmConfig();
      await saveLlmConfig({
        provider: cfg.provider,
        model: cfg.model,
        api_base: cfg.api_base || null,
        api_key_env: cfg.api_key_env || null,
        temperature: cfg.temperature,
        max_output_tokens: cfg.max_output_tokens,
        timeout_seconds: cfg.timeout_seconds,
        max_retries: cfg.max_retries,
        abstract_char_limit: cfg.abstract_char_limit,
        pdf_head_char_limit: cfg.pdf_head_char_limit,
        auto_tag_on_ingest: cfg.auto_tag_on_ingest,
        feed_categories: selectedCats,
      });
      setShowSettings(false);
    } catch (err) {
      console.error('Failed to save feed_categories:', err);
    } finally {
      setSaving(false);
    }
  }, [selectedCats]);

  const handleSelectPoint = useCallback(
    (info: { kind: string; arxivId: string; title?: string; abstract?: string } | null) => {
      setSelected(info);
    },
    [],
  );

  const handleCloseDrawer = useCallback(() => setSelected(null), []);

  const handleIngest = useCallback(async (arxivId: string) => {
    setIngesting(true);
    try {
      const result = await ingestFromFeed(arxivId);
      if (result.duplicate) {
        await showAlert('Already in library', 'This paper is already in your library.');
      } else if (result.accepted) {
        // Refresh map data so the new paper appears as a library star
        try {
          const d = await fetchMapData();
          setData(d);
        } catch { /* ignore refresh failure */ }
        setSelected(null); // close drawer on success
      } else {
        await showAlert('Failed to add paper', result.message || 'An unknown error occurred.');
      }
    } catch (err) {
      await showAlert('Ingest failed', (err as Error).message);
    } finally {
      setIngesting(false);
    }
  }, [showAlert]);

  if (error) {
    return (
      <div className="map-loading">
        <div style={{ textAlign: 'center' }}>
          <p style={{ color: '#ff4444', marginBottom: 8 }}>{'Failed to load map'}</p>
          <p style={{ fontSize: 13, color: '#888' }}>{error}</p>
          <p style={{ fontSize: 12, color: '#666', marginTop: 12 }}>
            {'Make sure the PaperPrism Agent is running.'}
          </p>
        </div>
      </div>
    );
  }

  if (!data) {
    return <div className="map-loading">Loading map…</div>;
  }

  const hasLibrary = data.library.length > 0;

  return (
    <>
      {dialogNode}
      <div className="map-container">
      <CanvasMap data={data} selectedId={selected?.arxivId ?? null} onSelectPoint={handleSelectPoint} />

      <div className="map-header">
        <div className="map-hero-brand">
          <img className="map-hero-logo" src="/icon/128.png" alt="PaperPrism" />
          <div className="map-hero-text">
            <div className="map-hero-title">
              Paper<span className="map-hero-prism">Prism</span>
              <span className="map-hero-atlas">Atlas</span>
            </div>
            <div className="map-hero-subtitle">Local Atlas · 127.0.0.1</div>
          </div>
        </div>
        <a href={chrome.runtime.getURL('dashboard.html')} className="map-header-link">
          <Icon name="chevron-left" size={12} aria-hidden />
          Dashboard
        </a>
        <button
          type="button"
          className={`map-settings-btn ${showSettings ? 'active' : ''}`}
          onClick={() => setShowSettings((v) => !v)}
          title="Configure feed categories"
        >
          <Icon name="settings" size={15} aria-hidden />
        </button>
      </div>

      {hasLibrary && (
        <div className="map-legend">
          <div className="item"><span className="dot library" /> Your Stars</div>
          {data.trajectory.length > 0 && (
            <div className="item"><span className="dot trajectory" /> Reading Path</div>
          )}
          {data.feed_hits.length > 0 && (
            <div className="item"><span className="dot feed" /> Distant Stars</div>
          )}
          {data.blind_spots.length > 0 && (
            <div className="item"><span className="dot blind-spot" /> Nebula</div>
          )}
        </div>
      )}

      <PointDrawer info={selected} onClose={handleCloseDrawer} onIngest={handleIngest} ingesting={ingesting} />

      {showSettings && (
        <div className="map-settings-panel">
          <div className="map-settings-header">
            <h3>Feed Categories</h3>
            <span className="map-settings-hint">Select arXiv categories for daily Distant Stars</span>
          </div>
          <div className="map-settings-body">
            {[...groupedCategories()].map(([group, cats]) => (
              <div key={group} className="map-settings-group">
                <div className="map-settings-group-label">{group}</div>
                <div className="map-settings-cats">
                  {cats.map((c: ArxivCategory) => (
                    <button
                      key={c.id}
                      type="button"
                      className={`map-cat-btn ${selectedCats.includes(c.id) ? 'selected' : ''}`}
                      onClick={() => toggleCat(c.id)}
                    >
                      <span className="map-cat-label">{c.label}</span>
                      <span className="map-cat-id">{c.id}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="map-settings-footer">
            <button type="button" className="map-cat-save" onClick={saveCategories} disabled={saving}>
              {saving ? 'Saving...' : 'Save & Apply'}
            </button>
            <button type="button" className="map-cat-cancel" onClick={() => setShowSettings(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
    </>
  );
}
