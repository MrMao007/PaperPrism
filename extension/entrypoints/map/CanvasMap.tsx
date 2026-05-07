import React, { useRef, useEffect, useCallback, useState } from 'react';
import type { MapData } from '../../lib/agent';

/** Color palette for different point kinds. */
const COLORS = {
  library: '#aaaaaa',
  trajectory: '#ff4444',
  feed: '#ffcc00',
  blind_spot: '#4488ff',
} as const;

interface CanvasMapProps {
  data: MapData;
  onSelectPoint: (info: { kind: string; arxivId: string; title?: string } | null) => void;
}

/** Build a lookup: arxivId → {x, y} from all point sets. */
function buildCoordMap(data: MapData): Map<string, { x: number; y: number }> {
  const m = new Map<string, { x: number; y: number }>();
  for (const p of data.library) m.set(p.arxiv_id, { x: p.x, y: p.y });
  for (const p of data.feed_hits) m.set(p.arxiv_id, { x: p.x, y: p.y });
  for (const p of data.blind_spots) m.set(p.arxiv_id, { x: p.x, y: p.y });
  return m;
}

/** Build arxivId → title lookup. */
function buildTitleMap(data: MapData): Map<string, string> {
  const m = new Map<string, string>();
  for (const p of data.library) {
    if (p.title) m.set(p.arxiv_id, p.title);
  }
  return m;
}

export default function CanvasMap({ data, onSelectPoint }: CanvasMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [hovered, setHovered] = useState<string | null>(null);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  const dragRef = useRef<{
    startX: number; startY: number;
    offsetX: number; offsetY: number;
    dragging: boolean;
  }>({ startX: 0, startY: 0, offsetX: 0, offsetY: 0, dragging: false });
  const coordMapRef = useRef<Map<string, { x: number; y: number }>>(buildCoordMap(data));
  const titleMapRef = useRef<Map<string, string>>(buildTitleMap(data));

  // Update lookups when data changes
  useEffect(() => {
    coordMapRef.current = buildCoordMap(data);
    titleMapRef.current = buildTitleMap(data);
  }, [data]);

  // Resize observer: sync canvas pixel size to container size
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;

    const sync = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      setCanvasSize({ w, h });
    };

    sync(); // initial sync
    const ro = new ResizeObserver(sync);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  // Auto-fit: scale & offset so library points fill the viewport
  useEffect(() => {
    if (canvasSize.w === 0 || canvasSize.h === 0) return;

    // Compute bounds from LIBRARY points only (not feed — too spread out)
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of data.library) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    // Include blind_spots in the fit (they are the "interesting" ones)
    for (const p of data.blind_spots) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }

    if (minX === Infinity) return; // no points at all

    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;
    const padding = 80;
    const w = canvasSize.w;
    const h = canvasSize.h;

    const s = Math.min((w - padding * 2) / rangeX, (h - padding * 2) / rangeY);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    setScale(s);
    setOffset({ x: w / 2 - cx * s, y: h / 2 - cy * s });
  }, [data, canvasSize]);

  // Draw
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvasSize.w;
    const h = canvasSize.h;
    if (w === 0 || h === 0) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const s = scale;
    const ox = offset.x;
    const oy = offset.y;

    // Data coords → screen coords
    const tx = (x: number) => x * s + ox;
    const ty = (y: number) => y * s + oy;

    // 1. Feed hits (yellow, drawn first as background)
    ctx.fillStyle = COLORS.feed;
    ctx.globalAlpha = 0.6;
    for (const p of data.feed_hits) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -10 || sx > w + 10 || sy < -10 || sy > h + 10) continue;
      ctx.beginPath();
      ctx.arc(sx, sy, hovered === p.arxiv_id ? 5 : 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // 2. Trajectory line (red)
    if (data.trajectory.length > 1) {
      ctx.strokeStyle = COLORS.trajectory;
      ctx.lineWidth = 2;
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      const firstCoord = coordMapRef.current.get(data.trajectory[0].arxiv_id);
      if (firstCoord) ctx.moveTo(tx(firstCoord.x), ty(firstCoord.y));
      for (let i = 1; i < data.trajectory.length; i++) {
        const c = coordMapRef.current.get(data.trajectory[i].arxiv_id);
        if (c) ctx.lineTo(tx(c.x), ty(c.y));
      }
      ctx.stroke();
      ctx.globalAlpha = 1;

      // Red dots on trajectory points
      ctx.fillStyle = COLORS.trajectory;
      for (const seg of data.trajectory) {
        const c = coordMapRef.current.get(seg.arxiv_id);
        if (c) {
          ctx.beginPath();
          ctx.arc(tx(c.x), ty(c.y), 5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    // 3. Library points (white/grey, larger)
    for (const p of data.library) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -10 || sx > w + 10 || sy < -10 || sy > h + 10) continue;
      const isHov = hovered === p.arxiv_id;
      // Subtle glow
      if (isHov) {
        const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 12);
        grad.addColorStop(0, 'rgba(170,170,170,0.4)');
        grad.addColorStop(1, 'rgba(170,170,170,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(sx, sy, 12, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = COLORS.library;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // 4. Blind spots (blue, glow)
    for (const p of data.blind_spots) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -10 || sx > w + 10 || sy < -10 || sy > h + 10) continue;

      const isHov = hovered === p.arxiv_id;
      const radius = isHov ? 18 : 14;
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, radius);
      grad.addColorStop(0, 'rgba(68, 136, 255, 0.6)');
      grad.addColorStop(1, 'rgba(68, 136, 255, 0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, radius, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = COLORS.blind_spot;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov ? 7 : 5, 0, Math.PI * 2);
      ctx.fill();
    }

    // 5. Hovered point label
    if (hovered) {
      const c = coordMapRef.current.get(hovered);
      const title = titleMapRef.current.get(hovered);
      if (c) {
        const sx = tx(c.x);
        const sy = ty(c.y);
        ctx.font = '12px -apple-system, BlinkMacSystemFont, sans-serif';
        const label = title || hovered;
        const displayLabel = label.length > 60 ? label.slice(0, 60) + '\u2026' : label;
        const textW = ctx.measureText(displayLabel).width;
        // Background pill
        ctx.fillStyle = 'rgba(17,17,24,0.9)';
        const pillX = sx - textW / 2 - 6;
        const pillY = sy - 30;
        const pillW = textW + 12;
        const pillH = 20;
        ctx.beginPath();
        ctx.roundRect(pillX, pillY, pillW, pillH, 4);
        ctx.fill();
        // Text
        ctx.fillStyle = '#fff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(displayLabel, sx, pillY + pillH / 2);
        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
      }
    }
  }, [data, offset, scale, hovered, canvasSize]);

  // Hit test: find nearest point within 10px
  const hitTest = useCallback(
    (clientX: number, clientY: number): { kind: string; arxivId: string; title?: string } | null => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      const mx = clientX - rect.left;
      const my = clientY - rect.top;
      const dataX = (mx - offset.x) / scale;
      const dataY = (my - offset.y) / scale;
      const threshold = 10 / scale;

      let best: { kind: string; arxivId: string; title?: string; dist: number } | null = null;

      // Check library first (they are more important)
      for (const p of data.library) {
        const d = Math.hypot(p.x - dataX, p.y - dataY);
        if (d < threshold && (!best || d < best.dist)) {
          best = { kind: 'library', arxivId: p.arxiv_id, title: p.title, dist: d };
        }
      }
      for (const p of data.blind_spots) {
        const d = Math.hypot(p.x - dataX, p.y - dataY);
        if (d < threshold && (!best || d < best.dist)) {
          best = { kind: 'blind_spot', arxivId: p.arxiv_id, dist: d };
        }
      }
      for (const p of data.feed_hits) {
        const d = Math.hypot(p.x - dataX, p.y - dataY);
        if (d < threshold && (!best || d < best.dist)) {
          best = { kind: 'feed', arxivId: p.arxiv_id, dist: d };
        }
      }
      return best ? { kind: best.kind, arxivId: best.arxivId, title: best.title } : null;
    },
    [data, scale, offset],
  );

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      offsetX: offset.x, offsetY: offset.y,
      dragging: false,
    };
  }, [offset]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const d = dragRef.current;
    if (e.buttons === 1) {
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) d.dragging = true;
      setOffset({ x: d.offsetX + dx, y: d.offsetY + dy });
    } else {
      const hit = hitTest(e.clientX, e.clientY);
      setHovered(hit?.arxivId ?? null);
      if (canvasRef.current) {
        canvasRef.current.style.cursor = hit ? 'pointer' : 'grab';
      }
    }
  }, [hitTest]);

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    const d = dragRef.current;
    if (!d.dragging) {
      const hit = hitTest(e.clientX, e.clientY);
      onSelectPoint(hit);
    }
    d.dragging = false;
  }, [hitTest, onSelectPoint]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const newScale = Math.max(0.01, Math.min(200, scale * factor));
    const newOffsetX = mx - (mx - offset.x) * (newScale / scale);
    const newOffsetY = my - (my - offset.y) * (newScale / scale);

    setScale(newScale);
    setOffset({ x: newOffsetX, y: newOffsetY });
  }, [scale, offset]);

  return (
    <div className="map-canvas-wrap" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
      />
    </div>
  );
}
