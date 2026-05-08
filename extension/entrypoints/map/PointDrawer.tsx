interface PointDrawerProps {
  info: { kind: string; arxivId: string; title?: string; abstract?: string } | null;
  onClose: () => void;
  onIngest?: (arxivId: string) => void;
  ingesting?: boolean;
}

export default function PointDrawer({ info, onClose, onIngest, ingesting }: PointDrawerProps) {
  if (!info) return <div className="map-drawer hidden" />;

  const kindLabel: Record<string, string> = {
    library: 'Your Star',
    feed: 'Distant Star',
    blind_spot: 'Nebula',
  };

  const isIngestible = info.kind === 'feed' || info.kind === 'blind_spot';

  return (
    <div className="map-drawer" style={{ position: 'relative' }}>
      <button className="close-btn" onClick={onClose} aria-label="Close">
        ×
      </button>

      <div style={{ fontSize: 11, color: '#6e9eff', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>
        {kindLabel[info.kind] || info.kind}
      </div>

      <h2>{info.title || 'Untitled'}</h2>

      <div className="arxiv-id">
        <a
          href={`https://arxiv.org/abs/${info.arxivId}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#6e9eff', textDecoration: 'none' }}
        >
          {info.arxivId}
        </a>
      </div>

      {info.abstract && (
        <div className="abstract">
          {info.abstract}
        </div>
      )}

      {isIngestible && (
        <button
          type="button"
          className="ingest-feed-btn"
          onClick={() => onIngest?.(info.arxivId)}
          disabled={ingesting}
        >
          {ingesting ? 'Adding\u2026' : 'Add to Library'}
        </button>
      )}

      {info.kind === 'blind_spot' && !info.abstract && (
        <div className="hint-box nebula">
          An unexplored region near your reading activity — a potential new direction.
        </div>
      )}

      {info.kind === 'feed' && !info.abstract && (
        <div className="hint-box distant">
          A recent paper in your research area, visible but not yet collected.
        </div>
      )}
    </div>
  );
}
