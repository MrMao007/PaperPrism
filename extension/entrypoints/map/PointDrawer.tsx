interface PointDrawerProps {
  info: { kind: string; arxivId: string; title?: string; abstract?: string } | null;
  onClose: () => void;
  onIngest?: (arxivId: string) => void;
  ingesting?: boolean;
}

const KIND_LABEL: Record<string, string> = {
  library: 'Your Star',
  feed: 'Distant Star',
  blind_spot: 'Nebula',
};

export default function PointDrawer({ info, onClose, onIngest, ingesting }: PointDrawerProps) {
  const isOpen = info !== null;
  const isIngestible = isOpen && (info.kind === 'feed' || info.kind === 'blind_spot');

  // Always render the container so the CSS transition can play out.
  // Content is rendered only when open to avoid stale data showing during close animation.
  return (
    <div className={`map-drawer${isOpen ? ' map-drawer--open' : ''}`}>
      {isOpen && (
        <div className="map-drawer-inner">
          <button className="close-btn" onClick={onClose} aria-label="Close">
            {'\u00d7'}
          </button>

          <div className="map-drawer-kind">
            {KIND_LABEL[info.kind] || info.kind}
          </div>

          <h2>{info.title || 'Untitled'}</h2>

          <div className="arxiv-id">
            <a
              href={`https://arxiv.org/abs/${info.arxivId}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {info.arxivId}
            </a>
          </div>

          {info.abstract && (
            <div className="abstract">{info.abstract}</div>
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
      )}
    </div>
  );
}
