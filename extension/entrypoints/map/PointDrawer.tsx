import React from 'react';

interface PointDrawerProps {
  info: { kind: string; arxivId: string; title?: string } | null;
  onClose: () => void;
}

export default function PointDrawer({ info, onClose }: PointDrawerProps) {
  if (!info) return <div className="map-drawer hidden" />;

  const kindLabel: Record<string, string> = {
    library: 'Library Paper',
    feed: 'arXiv Feed',
    blind_spot: 'Blind Spot',
  };

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

      {info.kind === 'blind_spot' && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(68,136,255,0.1)', borderRadius: 6, fontSize: 13 }}>
          This paper sits in a knowledge gap near your reading activity.
        </div>
      )}

      {info.kind === 'feed' && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(255,204,0,0.1)', borderRadius: 6, fontSize: 13 }}>
          This paper is from the recent arXiv feed and falls within your research area.
        </div>
      )}
    </div>
  );
}
