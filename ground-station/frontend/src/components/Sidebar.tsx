import { useAppStore } from '../stores/appStore';
import { Icon, IconFill } from './Icon';
import { useHardwareStatus } from '../hooks/useHardwareStatus';
import type { DeviceHealthState } from '../types';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: 'squares-four' as const },
  { id: 'livefeed', label: 'Live Feed', icon: 'video-camera' as const, badge: 'LIVE' },
  { id: 'detections', label: 'Detections', icon: 'crosshair' as const, hasAlert: true },
  { id: 'map', label: 'Map', icon: 'map-trifold' as const },
];

type RowState = 'online' | 'offline' | 'warning' | 'unknown';

/**
 * One hardware line in the Hardware Status card.
 *
 * The dot colour and the text are driven by the measured state - there is no
 * hardcoded or assumed status anywhere.
 */
function StatusRow({
  label,
  state,
  onlineText,
  offlineText,
}: {
  label: string;
  state: RowState;
  onlineText: string;
  offlineText: string;
}) {
  const dot =
    state === 'online'
      ? 'bg-green-500'
      : state === 'warning'
        ? 'bg-amber-500'
        : state === 'unknown'
          ? 'bg-slate-300'
          : 'bg-red-400';

  const text =
    state === 'online'
      ? 'text-green-700'
      : state === 'warning'
        ? 'text-amber-600'
        : state === 'unknown'
          ? 'text-slate-400'
          : 'text-red-500';

  const value =
    state === 'online'
      ? onlineText
      : state === 'warning'
        ? offlineText
        : state === 'unknown'
          ? 'Unknown'
          : offlineText;

  return (
    <div className="flex justify-between items-center gap-1 py-1 border-b border-slate-100 last:border-0">
      <span className="text-slate-500 text-[10px] flex items-center gap-1.5 min-w-0">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
        <span className="truncate">{label}</span>
      </span>
      <span className={`font-medium text-[9px] uppercase whitespace-nowrap ${text}`}>
        {value}
      </span>
    </div>
  );
}

/** Map a measured device state onto the row state used by the card. */
function rowStateFor(state: DeviceHealthState): RowState {
  switch (state) {
    case 'online':
      return 'online';
    case 'stale':
      return 'warning';
    case 'offline':
      return 'offline';
    default:
      return 'unknown';
  }
}

export function Sidebar() {
  const { activeTab, setActiveTab, alertAcknowledged, sidebarOpen, setSidebarOpen, environment, system } =
    useAppStore();

  // ── Real hardware states ────────────────────────────────────────────────
  // Every state comes from the backend's real measurements (see
  // useHardwareStatus). A device is only "Disconnected" when the backend
  // measured it offline; a previously-online device whose report stopped
  // arriving is shown as "Stale" (the link is the problem, not the hardware).
  const hw = useHardwareStatus();

  // System health is derived from the real measurements of the Pi. When the
  // hardware report is no longer fresh the last measurements are not current,
  // so the state is reported as unknown instead of a stale "Normal".
  const CPU_WARN_C = 80;
  const RAM_WARN_PCT = 90;
  const systemHealth: RowState =
    !hw.hardwareFresh || system == null
      ? 'unknown'
      : (system.cpu_temp_c != null && system.cpu_temp_c >= CPU_WARN_C) ||
          (system.ram_usage_pct != null && system.ram_usage_pct >= RAM_WARN_PCT)
        ? 'warning'
        : 'online';

  const getEnvironmentIcon = (condition: string) => {
    if (condition.includes('Rain')) return 'cloud';
    if (condition.includes('Overcast')) return 'cloud';
    if (condition.includes('Clear') || condition.includes('Sunny')) return 'cloud-sun';
    return 'cloud-sun';
  };

  return (
    <>
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-slate-900/50 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 w-64 bg-white border-r border-slate-200 flex flex-col z-50 transform transition-transform duration-300 lg:translate-x-0 lg:static ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="h-20 flex items-center justify-between px-6 border-b border-slate-100 shrink-0">
          <div className="flex items-center gap-3">
            <img src="/image/AERIS.png" alt="AERIS Logo" className="h-10 w-auto object-contain" />
            <div>
              <div className="font-bold text-lg leading-tight tracking-tight text-slate-800">AERIS</div>
              <p className="text-[9px] text-slate-500 uppercase tracking-wide">Off-Grid UAV System</p>
            </div>
          </div>
          <button
            className="lg:hidden text-slate-500 hover:text-slate-800"
            onClick={() => setSidebarOpen(false)}
          >
            <Icon name="x" className="text-xl" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto py-4 px-3 flex flex-col gap-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id as typeof activeTab)}
              className={`nav-btn w-full flex items-center justify-between px-4 py-2.5 rounded-lg transition-colors ${
                activeTab === item.id
                  ? 'active-nav bg-slate-50 text-slate-900'
                  : 'text-slate-600 hover:bg-slate-50'
              }`}
            >
              <div className="flex items-center gap-3">
                <Icon name={item.icon} className="text-lg" />
                <span className="font-medium">{item.label}</span>
              </div>
              {item.badge && (
                <span className="text-[10px] px-1.5 py-0.5 rounded font-bold bg-green-100 text-green-700">
                  {item.badge}
                </span>
              )}
              {item.hasAlert && !alertAcknowledged && (
                <span className="bg-danger text-white text-xs px-2 py-0.5 rounded-full font-bold">1</span>
              )}
            </button>
          ))}
        </nav>

        <div className="p-4 border-t border-slate-100 shrink-0">
          <h3 className="text-xs font-bold text-slate-400 mb-2.5 uppercase tracking-wider">Hardware Status</h3>
          <div className="bg-slate-50 rounded-xl px-3 py-2 border border-slate-100">
            {/* Essential component statuses only - every state is measured. */}
            <div className="space-y-0.5 text-xs">
              <StatusRow
                label="Raspberry Pi 4B"
                state={rowStateFor(hw.rpi)}
                onlineText="Online"
                offlineText={hw.rpi === 'stale' ? 'Stale' : 'Offline'}
              />
              <StatusRow
                label="MLX90640"
                state={rowStateFor(hw.thermal)}
                onlineText="Connected"
                offlineText={hw.thermal === 'stale' ? 'Stale' : 'Disconnected'}
              />
              <StatusRow
                label="SX1278 LoRa"
                state={rowStateFor(hw.lora)}
                onlineText="Connected"
                offlineText={hw.lora === 'stale' ? 'Stale' : 'Disconnected'}
              />
              {/* Only shown when it is not normal - the header strip already
                  carries the CPU/RAM figures. */}
              {systemHealth !== 'online' && (
                <StatusRow
                  label="System Health"
                  state={systemHealth}
                  onlineText="Normal"
                  offlineText="Warning"
                />
              )}
            </div>
          </div>

          <div className="mt-4 px-2">
            <h3 className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">Environment</h3>
            <div className="flex items-center gap-3">
              <IconFill name={getEnvironmentIcon(environment.condition) as any} className="text-2xl text-yellow-500" />
              <div>
                <div className="text-lg font-bold text-slate-800 leading-none">
                  {environment.temperature != null ? `${environment.temperature}°C` : '—'}
                </div>
                <div className="text-xs text-slate-500">{environment.condition}</div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
