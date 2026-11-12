import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { useHardwareStatus } from '../hooks/useHardwareStatus';

export type ThermalPalette = 'iron' | 'inferno' | 'rainbow' | 'grayscale';

export const THERMAL_PALETTES: { id: ThermalPalette; label: string }[] = [
  { id: 'iron', label: 'Ironbow' },
  { id: 'inferno', label: 'Inferno' },
  { id: 'rainbow', label: 'Rainbow' },
  { id: 'grayscale', label: 'Grayscale' },
];

// ── 256-entry RGB lookup tables, interpolated between anchor stops ──────────
type Stop = [number, [number, number, number]];

const PALETTE_STOPS: Record<ThermalPalette, Stop[]> = {
  iron: [
    [0.0, [0, 0, 0]],
    [0.2, [65, 0, 85]],
    [0.38, [145, 15, 55]],
    [0.55, [210, 62, 10]],
    [0.72, [240, 130, 20]],
    [0.86, [252, 210, 90]],
    [1.0, [255, 255, 255]],
  ],
  inferno: [
    [0.0, [0, 0, 4]],
    [0.13, [31, 12, 72]],
    [0.25, [85, 15, 109]],
    [0.38, [136, 34, 106]],
    [0.5, [186, 54, 85]],
    [0.63, [227, 89, 51]],
    [0.75, [249, 140, 10]],
    [0.88, [249, 201, 50]],
    [1.0, [252, 255, 164]],
  ],
  rainbow: [
    [0.0, [0, 0, 143]],
    [0.125, [0, 0, 255]],
    [0.375, [0, 255, 255]],
    [0.625, [255, 255, 0]],
    [0.875, [255, 0, 0]],
    [1.0, [128, 0, 0]],
  ],
  grayscale: [
    [0.0, [0, 0, 0]],
    [1.0, [255, 255, 255]],
  ],
};

function buildLut(stops: Stop[]): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let a = stops[0];
    let b = stops[stops.length - 1];
    for (let s = 0; s < stops.length - 1; s++) {
      if (t >= stops[s][0] && t <= stops[s + 1][0]) {
        a = stops[s];
        b = stops[s + 1];
        break;
      }
    }
    const span = Math.max(b[0] - a[0], 1e-6);
    const f = Math.min(1, Math.max(0, (t - a[0]) / span));
    lut[i * 3 + 0] = Math.round(a[1][0] + f * (b[1][0] - a[1][0]));
    lut[i * 3 + 1] = Math.round(a[1][1] + f * (b[1][1] - a[1][1]));
    lut[i * 3 + 2] = Math.round(a[1][2] + f * (b[1][2] - a[1][2]));
  }
  return lut;
}

const LUTS: Record<ThermalPalette, Uint8Array> = {
  iron: buildLut(PALETTE_STOPS.iron),
  inferno: buildLut(PALETTE_STOPS.inferno),
  rainbow: buildLut(PALETTE_STOPS.rainbow),
  grayscale: buildLut(PALETTE_STOPS.grayscale),
};

/** CSS gradient matching a palette (used for the legend strip). */
export function paletteGradient(palette: ThermalPalette): string {
  const stops = PALETTE_STOPS[palette];
  return `linear-gradient(90deg, ${stops
    .map(([t, [r, g, b]]) => `rgb(${r},${g},${b}) ${Math.round(t * 100)}%`)
    .join(', ')})`;
}

const COLS = 32;
const ROWS = 24;
const STALE_MS = 5000; // no fresh frame for 5 s -> treat as offline
const MIN_SPAN_C = 2;  // never auto-range tighter than 2 °C (avoids noise chase)

interface ThermalCanvasProps {
  /** card: fills its parent (Live Feed card). page: fills its parent with larger overlays. */
  variant?: 'card' | 'page';
  palette?: ThermalPalette;
  className?: string;
}

export function ThermalCanvas({ palette = 'iron', className = '' }: ThermalCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Offscreen canvases built once: raw 32x24 pixels + smoothing stages.
  const rawRef = useRef<HTMLCanvasElement | null>(null);
  const smoothRef = useRef<HTMLCanvasElement | null>(null);
  // EMA-smoothed display range so colors stay stable frame-to-frame.
  const rangeRef = useRef<{ min: number; max: number } | null>(null);
  // Temporal filter (light EMA) — kills shimmer while tracking motion with
  // minimal lag. Alpha = how fast a new frame overrides history; stats shown
  // to the user always come from the UNFILTERED sensor frame.
  // 0.65 at 4 Hz ≈ 0.4 s of effective lag — motion tracks promptly.
  const temporalRef = useRef<Float32Array | null>(null);
  const TEMPORAL_ALPHA = 0.65;

  const thermal = useAppStore((s) => s.thermal);
  const frame = useAppStore((s) => s.thermalFrame);
  const hw = useHardwareStatus();

  // 1 Hz ticker so the stale/offline overlay appears even without new frames.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => (n + 1) % 1000000), 1000);
    return () => window.clearInterval(id);
  }, []);

  const matrix = frame?.matrix ?? null;
  const frameTime = frame?.receivedAt ?? 0;
  // A frame only counts as live while it is recent AND the sensor is still
  // reported as answering by the backend.
  const live =
    hw.thermal === 'online' &&
    Boolean(thermal?.ok) &&
    matrix !== null &&
    frameTime > 0 &&
    Date.now() - frameTime < STALE_MS;

  // ── Paint the latest frame ─────────────────────────────────────────────
  const paint = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    if (w === 0 || h === 0) return;

    if (!matrix) {
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, w, h);
      return;
    }

    // Temporal filter: blend the new frame into the running average. The
    // sensor's own values remain the source of truth for stats/hotspot; the
    // filter only affects what the colormap paints.
    let f = temporalRef.current;
    if (!f || f.length !== matrix.length) {
      f = Float32Array.from(matrix);
    } else {
      const a = TEMPORAL_ALPHA;
      for (let i = 0; i < matrix.length; i++) {
        f[i] += a * (matrix[i] - f[i]);
      }
    }
    temporalRef.current = f;

    // Auto-range with EMA smoothing (display only - values shown are real).
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = 0; i < f.length; i++) {
      const v = f[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (!Number.isFinite(mn) || !Number.isFinite(mx)) return;
    if (mx - mn < MIN_SPAN_C) {
      const mid = (mx + mn) / 2;
      mn = mid - MIN_SPAN_C / 2;
      mx = mid + MIN_SPAN_C / 2;
    }
    const prev = rangeRef.current;
    if (!prev) {
      rangeRef.current = { min: mn, max: mx };
    } else {
      rangeRef.current = { min: prev.min + (mn - prev.min) * 0.5, max: prev.max + (mx - prev.max) * 0.5 };
    }
    const rMin = rangeRef.current.min;
    const span = Math.max(rangeRef.current.max - rMin, 1e-3);

    // Raw 32x24 pixel buffer
    if (!rawRef.current) {
      const c = document.createElement('canvas');
      c.width = COLS;
      c.height = ROWS;
      rawRef.current = c;
    }
    if (!smoothRef.current) {
      const c = document.createElement('canvas');
      c.width = COLS * 8;   // 256x192 intermediate (8x upscale, high-quality bilinear x2 stages)
      c.height = ROWS * 8;
      smoothRef.current = c;
    }
    const raw = rawRef.current.getContext('2d');
    const smooth = smoothRef.current.getContext('2d');
    if (!raw || !smooth) return;

    const img = raw.createImageData(COLS, ROWS);
    const lut = LUTS[palette];
    const px = img.data;
    for (let i = 0; i < 768; i++) {
      let t = (f[i] - rMin) / span;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const li = (t * 255) | 0;
      const o = i * 4;
      const lo = li * 3;
      px[o] = lut[lo];
      px[o + 1] = lut[lo + 1];
      px[o + 2] = lut[lo + 2];
      px[o + 3] = 255;
    }
    raw.putImageData(img, 0, 0);

    // Bilinear-smooth 32x24 -> 128x96 (keeps hotspots edge-aware enough at this
    // resolution while removing blocky pixels)
    smooth.imageSmoothingEnabled = true;
    smooth.imageSmoothingQuality = 'high';
    smooth.clearRect(0, 0, smoothRef.current.width, smoothRef.current.height);
    smooth.drawImage(rawRef.current, 0, 0, smoothRef.current.width, smoothRef.current.height);

    // Final upscale to the display canvas, aspect-correct (4:3)
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    const scale = Math.min(w / COLS, h / ROWS);
    const dw = Math.floor(COLS * scale);
    const dh = Math.floor(ROWS * scale);
    const dx = Math.floor((w - dw) / 2);
    const dy = Math.floor((h - dh) / 2);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    // 1px blur removes residual 8x8 tile grid without eating detail.
    try { ctx.filter = 'blur(1px)'; } catch { /* older engines: draw without filter */ }
    ctx.drawImage(smoothRef.current, dx, dy, dw, dh);
    try { ctx.filter = 'none'; } catch { /* ignore */ }

    // Hotspot marker
    const hx = frame?.hotspot_x;
    const hy = frame?.hotspot_y;
    if (typeof hx === 'number' && typeof hy === 'number') {
      const cx = dx + ((hx + 0.5) / COLS) * dw;
      const cy = dy + ((hy + 0.5) / ROWS) * dh;
      const r = Math.max(6, Math.min(dw, dh) * 0.035);
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth = Math.max(1.5, r * 0.22);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - r * 1.6, cy);
      ctx.lineTo(cx - r * 0.6, cy);
      ctx.moveTo(cx + r * 0.6, cy);
      ctx.lineTo(cx + r * 1.6, cy);
      ctx.moveTo(cx, cy - r * 1.6);
      ctx.lineTo(cx, cy - r * 0.6);
      ctx.moveTo(cx, cy + r * 0.6);
      ctx.lineTo(cx, cy + r * 1.6);
      ctx.stroke();
    }
  };

  // Re-paint on new frame / palette change / resize
  useEffect(() => {
    paint();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, palette]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      paint();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div ref={wrapRef} className={`relative h-full w-full overflow-hidden bg-white ${className}`}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-label="MLX90640 live thermal image" />

      {!live && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-white text-center px-4">
          <div className="text-slate-400 text-[10px] font-bold uppercase tracking-widest">MLX90640 Thermal</div>
          <div className="text-slate-700 text-sm font-semibold">Thermal sensor offline</div>
          <div className="text-slate-500 text-[11px] leading-snug max-w-[280px]">
            {thermal?.error ?? hw.devices?.mlx90640?.reason ?? 'Waiting for the first frame from the sensor…'}
          </div>
        </div>
      )}

    </div>
  );
}
