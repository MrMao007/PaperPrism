import React, { useRef, useEffect, useCallback, useState } from 'react';
import type { MapData } from '../../lib/agent';

/** Star-atlas color palette. */
const COLORS = {
  library: '#ffd966',       // warm gold — your collected stars
  libraryGlow: '#ffb347',   // amber glow halo
  trajectory: '#e8a44a',    // golden constellation line
  feed: '#8eafd0',          // cool blue-white — distant stars
  blind_spot: '#40e0d0',    // turquoise nebula — unexplored
  blind_spotGlow: '#00ced1', // dark turquoise glow
} as const;

interface CanvasMapProps {
  data: MapData;
  selectedId: string | null;
  onSelectPoint: (info: { kind: string; arxivId: string; title?: string; abstract?: string } | null) => void;
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
  for (const p of data.feed_hits) {
    if (p.title) m.set(p.arxiv_id, p.title);
  }
  for (const p of data.blind_spots) {
    if (p.title) m.set(p.arxiv_id, p.title);
  }
  return m;
}

/** Build arxivId → abstract lookup (feed & blind_spot only). */
function buildAbstractMap(data: MapData): Map<string, string> {
  const m = new Map<string, string>();
  for (const p of data.feed_hits) {
    if (p.abstract) m.set(p.arxiv_id, p.abstract);
  }
  for (const p of data.blind_spots) {
    if (p.abstract) m.set(p.arxiv_id, p.abstract);
  }
  return m;
}

/** Deterministic pseudo-random for background stars. */
function seededRandom(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

export default function CanvasMap({ data, selectedId, onSelectPoint }: CanvasMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [hovered, setHovered] = useState<string | null>(null);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  const [tick, setTick] = useState(0);
  // Track the currently animating center-pan so we can cancel it on next selection
  const centerAnimRef = useRef<number | null>(null);
  const dragRef = useRef<{
    startX: number; startY: number;
    offsetX: number; offsetY: number;
    dragging: boolean;
  }>({ startX: 0, startY: 0, offsetX: 0, offsetY: 0, dragging: false });
  const coordMapRef = useRef<Map<string, { x: number; y: number }>>(buildCoordMap(data));
  const titleMapRef = useRef<Map<string, string>>(buildTitleMap(data));
  const abstractMapRef = useRef<Map<string, string>>(buildAbstractMap(data));

  // Update lookups when data changes
  useEffect(() => {
    coordMapRef.current = buildCoordMap(data);
    titleMapRef.current = buildTitleMap(data);
    abstractMapRef.current = buildAbstractMap(data);
  }, [data]);

  // When a point is selected, smoothly pan the viewport so that point
  // sits at the canvas center. Uses a short eased animation (300 ms)
  // so the user can see where the view is moving, rather than jumping.
  const scaleRef = useRef(scale);
  const offsetRef = useRef(offset);
  useEffect(() => { scaleRef.current = scale; }, [scale]);
  useEffect(() => { offsetRef.current = offset; }, [offset]);

  useEffect(() => {
    if (!selectedId) return;
    const coord = coordMapRef.current.get(selectedId);
    if (!coord) return;
    if (canvasSize.w === 0 || canvasSize.h === 0) return;

    // Cancel any previous animation
    if (centerAnimRef.current !== null) {
      cancelAnimationFrame(centerAnimRef.current);
      centerAnimRef.current = null;
    }

    const startOffset = { ...offsetRef.current };
    const targetOffset = {
      x: canvasSize.w / 2 - coord.x * scaleRef.current,
      y: canvasSize.h / 2 - coord.y * scaleRef.current,
    };

    const duration = 350; // ms
    const startTime = performance.now();

    const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

    const step = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = easeOutCubic(progress);

      setOffset({
        x: startOffset.x + (targetOffset.x - startOffset.x) * eased,
        y: startOffset.y + (targetOffset.y - startOffset.y) * eased,
      });

      if (progress < 1) {
        centerAnimRef.current = requestAnimationFrame(step);
      } else {
        centerAnimRef.current = null;
      }
    };

    centerAnimRef.current = requestAnimationFrame(step);

    return () => {
      if (centerAnimRef.current !== null) {
        cancelAnimationFrame(centerAnimRef.current);
        centerAnimRef.current = null;
      }
    };
  }, [selectedId, canvasSize]);

  // Animation loop for subtle star twinkle
  useEffect(() => {
    let raf: number;
    const animate = () => {
      setTick((t) => t + 1);
      raf = requestAnimationFrame(animate);
    };
    raf = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(raf);
  }, []);

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

  // Auto-fit: scale & offset so library points fill the viewport.
  //
  // IMPORTANT: this effect must NOT run on every poll. The Atlas page
  // refreshes /api/map every 5 s, which produces a brand-new `data`
  // object reference even when its content is identical. If we depend
  // on `[data, canvasSize]`, every poll would call setScale/setOffset
  // and snap the viewport back to "fit-all", destroying any pan/zoom
  // the user just performed.
  //
  // Fit conditions:
  //   1. First time we have a non-empty point set (initial layout).
  //   2. Canvas size actually changed (window resize).
  //   3. Library/blind-spot membership changed (e.g. user just added a
  //      paper; new bounds may fall off-screen). Membership is keyed
  //      by sorted arxiv_ids, so reordering / coordinate jitter from
  //      UMAP does NOT trigger a refit.
  const fitSignatureRef = useRef<string>('');
  useEffect(() => {
    if (canvasSize.w === 0 || canvasSize.h === 0) return;

    const memberIds = [
      ...data.library.map((p) => p.arxiv_id),
      ...data.blind_spots.map((p) => `b:${p.arxiv_id}`),
    ].sort();
    const signature = `${canvasSize.w}x${canvasSize.h}|${memberIds.join(',')}`;
    if (signature === fitSignatureRef.current) return; // user-controlled view; leave it alone
    fitSignatureRef.current = signature;

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

    // ── 0. Deep space background ──────────────────────────────────
    const bgGrad = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
    bgGrad.addColorStop(0, '#0d0f1a');
    bgGrad.addColorStop(0.5, '#080a14');
    bgGrad.addColorStop(1, '#03040a');
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, w, h);

    // Subtle nebula tint near center
    ctx.globalAlpha = 0.08;
    const neb = ctx.createRadialGradient(w * 0.4, h * 0.5, 0, w * 0.4, h * 0.5, Math.max(w, h) * 0.4);
    neb.addColorStop(0, '#2a1a4e');
    neb.addColorStop(1, 'transparent');
    ctx.fillStyle = neb;
    ctx.fillRect(0, 0, w, h);
    const neb2 = ctx.createRadialGradient(w * 0.7, h * 0.3, 0, w * 0.7, h * 0.3, Math.max(w, h) * 0.3);
    neb2.addColorStop(0, '#1a2a4e');
    neb2.addColorStop(1, 'transparent');
    ctx.fillStyle = neb2;
    ctx.fillRect(0, 0, w, h);
    ctx.globalAlpha = 1;

    // Background star field (deterministic, fixed positions)
    const rand = seededRandom(42);
    const starCount = Math.floor(w * h / 800);
    for (let i = 0; i < starCount; i++) {
      const sx = rand() * w;
      const sy = rand() * h;
      const brightness = rand();
      const size = brightness < 0.95 ? 0.5 : (brightness < 0.99 ? 1 : 1.5);
      const twinkle = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(tick * 0.02 + i * 1.7));
      ctx.globalAlpha = (0.15 + brightness * 0.35) * twinkle;
      ctx.fillStyle = brightness > 0.97 ? '#c8d8ff' : '#ffffff';
      ctx.beginPath();
      ctx.arc(sx, sy, size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // ── 1. Feed hits — distant blue-white stars ────────────────────
    for (const p of data.feed_hits) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;
      const isHov = hovered === p.arxiv_id;
      const isSel = selectedId === p.arxiv_id;

      // Selection ring — pulsing blue orbit
      if (isSel) {
        const selPulse = 0.7 + 0.3 * Math.sin(tick * 0.06);
        const selR = 18 * selPulse;
        ctx.strokeStyle = `rgba(142, 175, 208, ${0.9 * selPulse})`;
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(sx, sy, selR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        // Outer diffuse glow ring
        const selGlow = ctx.createRadialGradient(sx, sy, selR * 0.5, sx, sy, selR * 1.6);
        selGlow.addColorStop(0, `rgba(142, 175, 208, ${0.2 * selPulse})`);
        selGlow.addColorStop(1, 'rgba(142, 175, 208, 0)');
        ctx.fillStyle = selGlow;
        ctx.beginPath();
        ctx.arc(sx, sy, selR * 1.6, 0, Math.PI * 2);
        ctx.fill();
      }

      // Soft glow halo — always visible, brighter on hover/selection
      const haloR = isHov || isSel ? 18 : 8;
      const haloAlpha = isHov || isSel ? 0.4 : 0.15;
      const haloGrad = ctx.createRadialGradient(sx, sy, 0, sx, sy, haloR);
      haloGrad.addColorStop(0, `rgba(142, 175, 208, ${haloAlpha})`);
      haloGrad.addColorStop(0.5, `rgba(100, 155, 200, ${haloAlpha * 0.4})`);
      haloGrad.addColorStop(1, 'rgba(100, 155, 200, 0)');
      ctx.fillStyle = haloGrad;
      ctx.beginPath();
      ctx.arc(sx, sy, haloR, 0, Math.PI * 2);
      ctx.fill();

      // Star core — brighter and larger
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = COLORS.feed;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov || isSel ? 4.5 : 2.5, 0, Math.PI * 2);
      ctx.fill();

      // Bright white center
      ctx.fillStyle = '#d0e8ff';
      ctx.globalAlpha = isHov || isSel ? 0.9 : 0.5;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov || isSel ? 2 : 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // ── 2. Trajectory — golden constellation line ──────────────────
    // Reduce raw events to ordered, deduplicated waypoints with known
    // coords. Folding consecutive duplicates avoids A→A→A self-loops
    // when the same paper is opened repeatedly; dropping unknown ids
    // (e.g. a paper just added but not yet embedded) prevents the
    // "ghost line" that previously shot from canvas (0,0) because
    // moveTo was skipped while subsequent lineTo calls still ran.
    const waypoints: { x: number; y: number; arxivId: string }[] = [];
    for (const seg of data.trajectory) {
      const c = coordMapRef.current.get(seg.arxiv_id);
      if (!c) continue;
      const last = waypoints[waypoints.length - 1];
      if (last && last.arxivId === seg.arxiv_id) continue;
      waypoints.push({ x: c.x, y: c.y, arxivId: seg.arxiv_id });
    }

    if (waypoints.length > 1) {
      ctx.strokeStyle = COLORS.trajectory;
      ctx.lineWidth = 1.2;
      ctx.globalAlpha = 0.45;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(tx(waypoints[0].x), ty(waypoints[0].y));
      for (let i = 1; i < waypoints.length; i++) {
        ctx.lineTo(tx(waypoints[i].x), ty(waypoints[i].y));
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    // Diamond markers on every reachable waypoint (also when there is
    // only a single point — a useful "you are here" cue).
    for (const wp of waypoints) {
      const sx = tx(wp.x);
      const sy = ty(wp.y);
      ctx.fillStyle = COLORS.trajectory;
      ctx.globalAlpha = 0.8;
      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(Math.PI / 4);
      ctx.fillRect(-3, -3, 6, 6);
      ctx.restore();
      ctx.globalAlpha = 1;
    }

    // ── 3. Blind spots — turquoise nebula ──────────────────────────
    for (const p of data.blind_spots) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;

      const isHov = hovered === p.arxiv_id;
      const isSel = selectedId === p.arxiv_id;
      const pulse = 0.8 + 0.2 * Math.sin(tick * 0.04 + p.x * 10);
      const outerR = (isHov || isSel ? 26 : 20) * pulse;
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, outerR);
      grad.addColorStop(0, `rgba(64, 224, 208, ${0.35 * pulse})`);
      grad.addColorStop(0.5, `rgba(0, 206, 209, ${0.15 * pulse})`);
      grad.addColorStop(1, 'rgba(0, 206, 209, 0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, outerR, 0, Math.PI * 2);
      ctx.fill();

      // Selection ring
      if (isSel) {
        const selPulse = 0.7 + 0.3 * Math.sin(tick * 0.06);
        ctx.strokeStyle = `rgba(64, 224, 208, ${0.85 * selPulse})`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(sx, sy, 14 * selPulse, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Core
      ctx.fillStyle = COLORS.blind_spot;
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov || isSel ? 5 : 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // ── 4. Library — bright golden stars ───────────────────────────
    for (const p of data.library) {
      const sx = tx(p.x);
      const sy = ty(p.y);
      if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
      const isHov = hovered === p.arxiv_id;
      const isSel = selectedId === p.arxiv_id;

      // Selection ring — pulsing gold orbit around the chosen star
      if (isSel) {
        const selPulse = 0.7 + 0.3 * Math.sin(tick * 0.06);
        const selR = 22 * selPulse;
        ctx.strokeStyle = `rgba(255, 220, 80, ${0.9 * selPulse})`;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.arc(sx, sy, selR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        // Outer diffuse glow ring
        const selGlow = ctx.createRadialGradient(sx, sy, selR * 0.6, sx, sy, selR * 1.5);
        selGlow.addColorStop(0, `rgba(255, 200, 60, ${0.25 * selPulse})`);
        selGlow.addColorStop(1, 'rgba(255, 200, 60, 0)');
        ctx.fillStyle = selGlow;
        ctx.beginPath();
        ctx.arc(sx, sy, selR * 1.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Outer glow halo
      const haloR = isHov || isSel ? 28 : 16;
      const haloGrad = ctx.createRadialGradient(sx, sy, 0, sx, sy, haloR);
      haloGrad.addColorStop(0, `rgba(255, 179, 71, ${isHov || isSel ? 0.45 : 0.2})`);
      haloGrad.addColorStop(0.4, `rgba(255, 217, 102, ${isHov || isSel ? 0.2 : 0.08})`);
      haloGrad.addColorStop(1, 'rgba(255, 179, 71, 0)');
      ctx.fillStyle = haloGrad;
      ctx.beginPath();
      ctx.arc(sx, sy, haloR, 0, Math.PI * 2);
      ctx.fill();

      // Lens-flare cross on hover or selection
      if (isHov || isSel) {
        ctx.strokeStyle = 'rgba(255, 230, 160, 0.35)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(sx - 20, sy); ctx.lineTo(sx + 20, sy);
        ctx.moveTo(sx, sy - 20); ctx.lineTo(sx, sy + 20);
        ctx.stroke();
        // Diagonal flare
        ctx.globalAlpha = 0.2;
        ctx.beginPath();
        ctx.moveTo(sx - 12, sy - 12); ctx.lineTo(sx + 12, sy + 12);
        ctx.moveTo(sx + 12, sy - 12); ctx.lineTo(sx - 12, sy + 12);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // Star core
      ctx.fillStyle = COLORS.library;
      ctx.beginPath();
      ctx.arc(sx, sy, isHov || isSel ? 5.5 : 3.5, 0, Math.PI * 2);
      ctx.fill();

      // Bright center
      ctx.fillStyle = '#fff8e0';
      ctx.beginPath();
      ctx.arc(sx, sy, isHov || isSel ? 2.5 : 1.5, 0, Math.PI * 2);
      ctx.fill();
    }

    // ── 5. Hovered point label — floating star-name ────────────────
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
        // Floating label with subtle glow
        const pillX = sx - textW / 2 - 8;
        const pillY = sy - 34;
        const pillW = textW + 16;
        const pillH = 22;
        // Glow behind pill
        ctx.globalAlpha = 0.3;
        ctx.shadowColor = 'rgba(255, 217, 102, 0.4)';
        ctx.shadowBlur = 12;
        ctx.fillStyle = 'rgba(12, 14, 28, 0.92)';
        ctx.beginPath();
        ctx.roundRect(pillX, pillY, pillW, pillH, 6);
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1;
        // Border
        ctx.strokeStyle = 'rgba(255, 217, 102, 0.25)';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.roundRect(pillX, pillY, pillW, pillH, 6);
        ctx.stroke();
        // Text
        ctx.fillStyle = '#ffe8a0';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(displayLabel, sx, pillY + pillH / 2);
        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
      }
    }
  }, [data, offset, scale, hovered, selectedId, canvasSize, tick]);

  // Hit test: find nearest point within 10px
  const hitTest = useCallback(
    (clientX: number, clientY: number): { kind: string; arxivId: string; title?: string; abstract?: string } | null => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      const mx = clientX - rect.left;
      const my = clientY - rect.top;
      const dataX = (mx - offset.x) / scale;
      const dataY = (my - offset.y) / scale;
      const threshold = 10 / scale;

      let best: { kind: string; arxivId: string; title?: string; abstract?: string; dist: number } | null = null;

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
          best = { kind: 'blind_spot', arxivId: p.arxiv_id, title: p.title, abstract: abstractMapRef.current.get(p.arxiv_id), dist: d };
        }
      }
      for (const p of data.feed_hits) {
        const d = Math.hypot(p.x - dataX, p.y - dataY);
        if (d < threshold && (!best || d < best.dist)) {
          best = { kind: 'feed', arxivId: p.arxiv_id, title: p.title, abstract: abstractMapRef.current.get(p.arxiv_id), dist: d };
        }
      }
      return best ? { kind: best.kind, arxivId: best.arxivId, title: best.title, abstract: best.abstract } : null;
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
    // No meaningful upper bound — UMAP coordinates are arbitrary floats,
    // so the auto-fit scale can already be in the hundreds. Capping at
    // any small absolute number (e.g. 200) would immediately block zoom.
    // Lower bound 0.001 prevents divide-by-zero while still allowing
    // extreme zoom-out to see all feed stars at once.
    const newScale = Math.max(0.001, scale * factor);
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
