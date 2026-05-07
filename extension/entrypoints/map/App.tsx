import React, { useEffect, useState, useCallback } from 'react';
import { fetchMapData, type MapData } from '../../lib/agent';
import CanvasMap from './CanvasMap';
import PointDrawer from './PointDrawer';

export default function MapApp() {
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{
    kind: string;
    arxivId: string;
    title?: string;
  } | null>(null);

  useEffect(() => {
    fetchMapData()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => {
        setError(err.message || 'Failed to load map');
      });
  }, []);

  const handleSelectPoint = useCallback(
    (info: { kind: string; arxivId: string; title?: string } | null) => {
      setSelected(info);
    },
    [],
  );

  const handleCloseDrawer = useCallback(() => setSelected(null), []);

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
    <div className="map-container">
      <CanvasMap data={data} onSelectPoint={handleSelectPoint} />

      <div className="map-header">
        <h1>PaperPrism Map</h1>
        <a href={chrome.runtime.getURL('dashboard.html')}>{'\u2190 Dashboard'}</a>
      </div>

      {hasLibrary && (
        <div className="map-legend">
          <div className="item"><span className="dot library" /> Library</div>
          {data.trajectory.length > 0 && (
            <div className="item"><span className="dot trajectory" /> Trajectory</div>
          )}
          {data.feed_hits.length > 0 && (
            <div className="item"><span className="dot feed" /> arXiv Feed</div>
          )}
          {data.blind_spots.length > 0 && (
            <div className="item"><span className="dot blind-spot" /> Blind Spot</div>
          )}
        </div>
      )}

      <PointDrawer info={selected} onClose={handleCloseDrawer} />
    </div>
  );
}
