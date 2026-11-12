import { useAppStore } from '../stores/appStore';
import { useHardwareStatus } from '../hooks/useHardwareStatus';

/**
 * RPi System Health — compact strip for the top navbar.
 *
 * Every value comes from the live Raspberry Pi 4B report that the backend
 * publishes over the WebSocket (the "system" broadcast, every 2 seconds), so
 * the figures update automatically. When no fresh report has arrived the
 * fields show N/A instead of repeating stale numbers.
 */
export function RpiSystemHealth() {
  const { system } = useAppStore();
  const hw = useHardwareStatus();

  const na = (v: number | null | undefined, suffix = '') =>
    v == null || !Number.isFinite(v) ? 'N/A' : `${v}${suffix}`;

  // Without a fresh hardware report the last measurements are out of date -
  // they must not be presented as current readings.
  const s = hw.hardwareFresh ? system : null;

  const uptime =
    s?.uptime_s != null
      ? `${Math.floor(s.uptime_s / 3600)}h ${Math.floor((s.uptime_s % 3600) / 60)}m`
      : 'N/A';

  const loadAvg = s?.loadavg?.length
    ? s.loadavg.map((v) => v.toFixed(1)).join(' / ')
    : 'N/A';

  return (
    <div className="hidden md:flex flex-1 min-w-0 mx-4 overflow-x-auto hide-scrollbar items-center gap-4 lg:gap-6 px-2 lg:px-4 text-xs">
      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">CPU Temp</div>
        <div className="font-mono font-semibold text-slate-800">{na(s?.cpu_temp_c, '°C')}</div>
      </div>
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">CPU Usage</div>
        <div className="font-mono font-semibold text-slate-800">{na(s?.cpu_usage_pct, '%')}</div>
      </div>
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">RAM Usage</div>
        <div className="font-mono font-semibold text-slate-800">{na(s?.ram_usage_pct, '%')}</div>
      </div>
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">Uptime</div>
        <div className="font-mono font-semibold text-slate-800">{uptime}</div>
      </div>
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">CPU Cores</div>
        <div className="font-mono font-semibold text-slate-800">{na(s?.cpu_count)}</div>
      </div>
      <div className="w-px h-6 bg-slate-200 shrink-0" />

      <div className="shrink-0">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">Load Avg</div>
        <div className="font-mono font-semibold text-slate-800">{loadAvg}</div>
      </div>
    </div>
  );
}
