import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { ThermalCanvas } from './ThermalCanvas';
import { useHardwareStatus } from '../hooks/useHardwareStatus';

/**
 * Live Feed card — three stacked sections:
 *   top    : LIVE FEED + live indicator
 *   centre : the thermal camera canvas (floating, rounded, exact 4:3)
 *   bottom : camera model + the measured frame rate
 *
 * NOTE: `.panel-card` is plain CSS declared after the Tailwind utilities, so
 * its background/padding win over utility classes. The dark canvas surface is
 * therefore applied inline (padding 0 + dark background) to guarantee the
 * intended dark theme regardless of the cascade order.
 */
export function LiveFeed() {
  const { thermal, thermalFrame } = useAppStore();
  const hw = useHardwareStatus();

  // "LIVE" only while the thermal camera really delivers frames - a stale
  // "LIVE" badge would hide a sensor that stopped answering. A stale link is
  // shown as "STALE", never as a hardware "OFFLINE".
  const live = hw.thermal === 'online' && Boolean(thermal?.ok);
  const stale = hw.thermal === 'stale';
  // Real, measured frame rate of the sensor feed (never a hardcoded number).
  const fps = thermalFrame?.fps ?? thermal?.fps ?? null;

  // The floating canvas keeps the sensor's 4:3 aspect ratio and uses as much
  // of the available centre space as possible (measured, not guessed).
  const areaRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const availW = Math.max(0, r.width - 24);
      const availH = Math.max(0, r.height - 24);
      // Largest 4:3 box that fits both dimensions.
      let h = availH;
      let w = h * (4 / 3);
      if (w > availW) {
        w = availW;
        h = w * (3 / 4);
      }
      setFrame({ width: Math.round(w), height: Math.round(h) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      className="lg:col-span-4 panel-card overflow-hidden relative group min-h-[300px] lg:min-h-0 flex flex-col"
      style={{ padding: 0, backgroundColor: '#0b0f16', borderColor: 'rgba(255,255,255,0.08)' }}
    >
      {/* ── Top: title + live indicator ─────────────────────────────────── */}
      <div className="relative z-10 flex items-center justify-between px-4 pt-3.5 pb-2.5 shrink-0">
        <h2 className="text-sm font-bold text-white tracking-wide">LIVE FEED</h2>
        <div className="flex items-center gap-1.5 bg-white/10 backdrop-blur-sm px-2 py-1 rounded-full text-[10px] font-bold text-white">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              live ? 'bg-green-400 pulse-indicator' : stale ? 'bg-amber-400' : 'bg-red-400'
            }`}
          />
          <span>{live ? 'LIVE' : stale ? 'STALE' : 'OFFLINE'}</span>
        </div>
      </div>

      {/* ── Centre: thermal canvas ──────────────────────────────────────── */}
      <div
        ref={areaRef}
        className="relative flex-1 min-h-0 overflow-hidden flex items-center justify-center bg-white"
      >
        {/* Floating panel - rounded, shadowed, exact 4:3 (measured) */}
        {frame.width > 0 && (
          <div
            className="relative rounded-2xl overflow-hidden ring-1 ring-white/15 border border-white/10 shadow-[0_24px_60px_rgba(0,0,0,0.75),0_6px_18px_rgba(0,0,0,0.55)] bg-white"
            style={{ width: frame.width, height: frame.height }}
          >
            <ThermalCanvas palette="iron" />
          </div>
        )}
      </div>

      {/* ── Bottom: camera model + measured FPS ─────────────────────────── */}
      <div className="relative z-10 flex items-center justify-between gap-2 px-4 py-2.5 shrink-0">
        <span className="text-[11px] font-semibold text-white/90 tracking-wide">
          {thermal?.model ?? 'MLX90640'}
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-white/70">
          <span className={`w-1.5 h-1.5 rounded-full ${live ? 'bg-emerald-400' : 'bg-slate-500'}`} />
          {fps != null ? `${fps.toFixed(1)} FPS` : '— FPS'}
        </span>
      </div>
    </div>
  );
}
