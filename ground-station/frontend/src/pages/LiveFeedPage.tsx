import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { Icon } from '../components/Icon';
import { ThermalCanvas, THERMAL_PALETTES, paletteGradient, type ThermalPalette } from '../components/ThermalCanvas';

const na = (v: number | null | undefined, suffix = '') =>
  v == null ? 'N/A' : `${v.toFixed(1)}${suffix}`;

export function LiveFeedPage() {
  const { link, thermal, thermalFrame } = useAppStore();
  const [palette, setPalette] = useState<ThermalPalette>('iron');
  const cardRef = useRef<HTMLDivElement>(null);

  // The floating canvas keeps the sensor's 4:3 aspect ratio and uses as much
  // of the available space as possible (measured, not guessed).
  const stageRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const availW = Math.max(0, r.width - 48);
      const availH = Math.max(0, r.height - 80 - 128);
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

  const goFullscreen = useCallback(() => {
    const el = cardRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
      return;
    }
    el.requestFullscreen?.().catch(() => {});
  }, []);

  const minC = thermalFrame?.min_c ?? thermal?.min_c ?? null;
  const maxC = thermalFrame?.max_c ?? thermal?.max_c ?? null;
  const meanC = thermalFrame?.mean_c ?? thermal?.mean_c ?? null;
  const hotspotC = thermalFrame?.hotspot_c ?? thermal?.hotspot_c ?? null;
  const hotspotX = thermalFrame?.hotspot_x ?? thermal?.hotspot_x ?? null;
  const hotspotY = thermalFrame?.hotspot_y ?? thermal?.hotspot_y ?? null;
  // Real, measured frame rate of the sensor feed.
  const fps = thermalFrame?.fps ?? thermal?.fps ?? null;
  const live = Boolean(thermal?.ok);

  return (
    <div className="h-full flex flex-col">
      <div
        ref={cardRef}
        className="panel-card flex-1 flex flex-col overflow-hidden border-none rounded-xl shadow-lg relative"
        style={{ padding: 0, backgroundColor: '#0b0f16' }}
      >
        <div className="p-4 flex flex-col sm:flex-row justify-between sm:items-center gap-4 bg-black/50 absolute top-0 left-0 right-0 z-10 text-white backdrop-blur">
          <div className="flex items-center gap-4">
            <h2 className="text-base sm:text-lg font-bold">MLX90640 Thermal Surveillance</h2>
            <span
              className={`${
                live ? 'bg-green-500 animate-pulse' : 'bg-red-500'
              } text-white text-[10px] px-2 py-0.5 rounded font-bold uppercase tracking-wider`}
            >
              {live ? 'Live' : 'Offline'}
            </span>
            {live && thermal?.human_detected && (
              <span className="bg-cyan-500 text-black text-[10px] px-2 py-0.5 rounded font-bold uppercase tracking-wider animate-pulse">
                Human Detected
              </span>
            )}
          </div>
          <div className="flex items-center gap-4 sm:gap-6 text-xs text-slate-300 overflow-x-auto pb-1 sm:pb-0">
            <div className="whitespace-nowrap">Res: <span className="text-white">{thermal?.resolution ?? '32x24'}</span></div>
            <div className="whitespace-nowrap">Rate: <span className="text-white">{fps != null ? `${fps.toFixed(1)} FPS` : 'N/A'}</span></div>
            <div className="whitespace-nowrap">Latency: <span className="text-green-400 font-mono">{link.wifi.latency != null ? Math.round(link.wifi.latency) + 'ms' : 'N/A'}</span></div>
            <div className="whitespace-nowrap">RSSI: <span className="text-white font-mono">{link.wifi.rssi != null ? Math.round(link.wifi.rssi) + 'dBm' : 'N/A'}</span></div>
            <button onClick={goFullscreen} className="hover:text-white rounded" aria-label="Toggle fullscreen">
              <Icon name="corners-out" className="text-xl" />
            </button>
          </div>
        </div>

        <div className="absolute inset-0 bg-[#0b0f16] overflow-hidden">
          {/* Page backdrop — visible around the floating thermal panel.
              The navigation bar and the control panels keep their black
              backgrounds; only the canvas surface itself is white. */}
          <div
            className="absolute inset-0"
            style={{ background: 'radial-gradient(130% 95% at 50% 0%, #232c38 0%, #161d27 45%, #0b0f16 100%)' }}
          />
          {/* Ambient glow behind the floating panel */}
          <div
            className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 h-[72%] aspect-[4/3] rounded-full"
            style={{ background: 'radial-gradient(closest-side, rgba(125, 211, 252, 0.13), transparent 70%)' }}
          />
          {/* Floating thermal panel (rounded, shadowed, smaller than the page) */}
          <div ref={stageRef} className="absolute inset-0 z-[1] flex items-center justify-center px-6 pt-20 pb-32">
            {frame.width > 0 && (
              <div
                className="relative rounded-2xl overflow-hidden ring-1 ring-white/15 border border-white/10 shadow-[0_30px_70px_rgba(0,0,0,0.8),0_8px_24px_rgba(0,0,0,0.6)] bg-white"
                style={{ width: frame.width, height: frame.height }}
              >
                <ThermalCanvas palette={palette} />
              </div>
            )}
          </div>
        </div>

        {/* Control + telemetry overlay */}
        <div className="absolute left-4 right-4 bottom-4 z-10 flex flex-col lg:flex-row lg:items-end gap-3 pointer-events-none">
          {/* Palette controls */}
          <div className="pointer-events-auto bg-black/60 backdrop-blur rounded-lg p-3 text-white w-full lg:w-auto">
            <div className="text-[9px] uppercase tracking-widest text-slate-300 font-bold mb-2">Color Palette</div>
            <div className="flex flex-wrap gap-1.5">
              {THERMAL_PALETTES.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPalette(p.id)}
                  className={`px-3 py-1.5 rounded text-[11px] font-semibold transition-colors border ${
                    palette === p.id
                      ? 'border-cyan-400 bg-cyan-400/20 text-cyan-300'
                      : 'border-white/15 bg-white/5 text-slate-200 hover:bg-white/10'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {/* Legend strip mapped to the live frame's real temperature range */}
            <div className="mt-2">
              <div
                className="h-2.5 w-full lg:w-64 rounded-sm"
                style={{ background: paletteGradient(palette) }}
              />
              <div className="mt-1 flex justify-between font-mono text-[9px] text-slate-300">
                <span>{na(minC)}°C</span>
                <span>{na(meanC)}°C</span>
                <span>{na(maxC)}°C</span>
              </div>
            </div>
          </div>

          {/* Temperature readout */}
          <div className="pointer-events-auto bg-black/60 backdrop-blur rounded-lg p-3 text-white grid grid-cols-2 sm:grid-cols-4 gap-3 lg:ml-auto min-w-[280px]">
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-300 font-bold">Min</div>
              <div className="font-mono font-bold text-base text-cyan-300">{na(minC, '°C')}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-300 font-bold">Max</div>
              <div className="font-mono font-bold text-base text-red-400">{na(maxC, '°C')}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-300 font-bold">Avg</div>
              <div className="font-mono font-bold text-base text-yellow-200">{na(meanC, '°C')}</div>
            </div>
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-300 font-bold">Hotspot</div>
              <div className="font-mono font-bold text-base text-white">
                {na(hotspotC, '°C')}
                <span className="block text-[9px] font-normal text-slate-400">
                  {hotspotX != null && hotspotY != null ? `px (${hotspotX}, ${hotspotY})` : 'px N/A'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Sensor status footer line */}
        <div className="absolute right-4 bottom-32 lg:bottom-auto lg:top-[76px] z-10 text-right pointer-events-none hidden md:block">
          <div className="bg-black/40 backdrop-blur rounded px-2 py-1 font-mono text-[10px] text-slate-300">
            MLX90640 @ {thermal?.address ?? '0x33'} · frame #{thermal?.frames ?? 0}
            {thermal?.error && <span className="block text-red-400">{thermal.error}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
