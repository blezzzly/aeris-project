import { useAppStore } from '../stores/appStore';
import { useHardwareStatus } from '../hooks/useHardwareStatus';

// Thermal Analytics strip - real MLX90640 data (I2C @0x33) delivered by the
// backend over the "system" broadcast and the live thermal frames.
export function ThermalAnalytics() {
  const { thermal } = useAppStore();
  const hw = useHardwareStatus();

  const na = (v: number | null | undefined, suffix = '') => (v == null ? 'N/A' : `${v}${suffix}`);
  const live = hw.thermal === 'online' && Boolean(thermal?.ok);
  const stale = hw.thermal === 'stale';
  const statusLabel = live ? '● LIVE' : stale ? 'STALE' : 'DISCONNECTED';
  const range = live && thermal?.min_c != null && thermal?.max_c != null
    ? `${thermal.min_c.toFixed(1)} – ${thermal.max_c.toFixed(1)}°C`
    : stale
      ? 'STALE'
      : 'DISCONNECTED';

  return (
    <div className="panel-card p-4">
      <h2 className="text-xs font-bold text-slate-500 mb-3 uppercase tracking-wider flex items-center justify-between">
        Thermal Analytics — MLX90640
        <span className={`text-[10px] font-bold ${live ? 'text-aeris' : stale ? 'text-amber-600' : 'text-slate-400'}`}>
          {statusLabel}
        </span>
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200"><div className="text-slate-400 text-[9px] uppercase">Range (min–max)</div><div className="font-mono font-bold text-slate-800 mt-1">{range}</div></div>
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200"><div className="text-slate-400 text-[9px] uppercase">Average</div><div className="font-mono font-bold text-slate-800 mt-1">{live ? `${thermal?.mean_c?.toFixed(1)}°C` : 'N/A'}</div></div>
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200"><div className="text-slate-400 text-[9px] uppercase">Hotspot</div><div className="font-mono font-bold text-slate-800 mt-1">{live ? `${thermal?.hotspot_c?.toFixed(1)}°C` : 'N/A'}</div></div>
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200"><div className="text-slate-400 text-[9px] uppercase">Hotspot XY</div><div className="font-mono font-bold text-slate-800 mt-1">({na(thermal?.hotspot_x)}, {na(thermal?.hotspot_y)})</div></div>
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200"><div className="text-slate-400 text-[9px] uppercase">Sensor / Frames</div><div className="font-mono font-bold text-slate-800 mt-1">{thermal?.resolution ?? '32x24'} @ {thermal?.address ?? '0x33'} · {na(thermal?.frames)}</div></div>
      </div>
      {thermal?.error && <div className="text-[11px] text-red-500 mt-2">{thermal.error}</div>}
      {!thermal?.error && stale && (
        <div className="text-[11px] text-amber-600 mt-2">
          Last measured state was online; no fresh hardware report
          {hw.reportAgeS != null ? ` for ${Math.round(hw.reportAgeS)} s` : ''}. The link is stale, not the sensor.
        </div>
      )}
      {!thermal?.error && !stale && hw.devices?.mlx90640?.reason && !live && (
        <div className="text-[11px] text-slate-500 mt-2">{hw.devices.mlx90640.reason}</div>
      )}
    </div>
  );
}
