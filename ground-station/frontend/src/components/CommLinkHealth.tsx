import { useAppStore } from '../stores/appStore';
import { Icon } from './Icon';
import { useHardwareStatus } from '../hooks/useHardwareStatus';

/**
 * Comm Links card.
 *
 * Every value shown here comes from the real hardware report delivered by the
 * backend over the WebSocket. When the radio is not connected, or no packet
 * has been received yet, the field shows N/A / Disconnected instead of a
 * fabricated quality figure.
 */
export function CommLinkHealth() {
  const { hardware, loraHardware, loraRx } = useAppStore();
  const hw = useHardwareStatus();

  const radio = hardware?.lora_sx1278;
  const model = loraHardware?.model ?? radio?.model ?? null;
  const frequency = loraHardware?.frequency_mhz ?? radio?.frequency_mhz ?? null;
  // The radio only counts as detected when it really answered on the SPI bus
  // and the report is still current.
  const detected = hw.lora === 'online' && Boolean(loraHardware?.available ?? radio?.connected);

  // Real reception measurements - null until a packet was actually received.
  // They are also dropped when the report is no longer current, so a stale
  // RSSI/SNR is never presented as a live measurement.
  const rssi = hw.linkFresh ? (radio?.rssi_dbm ?? loraRx?.last_rssi ?? null) : null;
  const snr = hw.linkFresh ? (radio?.snr_db ?? loraRx?.last_snr ?? null) : null;
  const rxCountRaw = hw.linkFresh ? (radio?.rx_packet_count ?? loraRx?.rx_count ?? 0) : 0;
  const lastRx = hw.linkFresh ? (radio?.last_rx ?? loraRx?.last_rx ?? null) : null;
  // Per requirement: show N/A until at least one valid packet has been
  // received - a counter of 0 means "nothing heard since boot", shown as N/A.
  const rxCount = rxCountRaw > 0 ? rxCountRaw : null;
  const lastRxLabel = (() => {
    if (!lastRx) return 'N/A';
    const d = new Date(lastRx);
    return Number.isNaN(d.getTime()) ? 'N/A' : d.toLocaleTimeString();
  })();

  const na = (v: number | null | undefined, unit = '') =>
    v == null || !Number.isFinite(v) ? 'N/A' : `${v}${unit}`;

  return (
    <div className="lg:col-span-3 flex flex-col gap-4 overflow-y-auto">
      <div className="panel-card flex-1 p-4">
        <h2 className="text-xs font-bold text-slate-500 mb-3 uppercase tracking-wider flex items-center justify-between">
          Comm Links
          <Icon name="broadcast" className="text-lg" />
        </h2>

        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
          <div className="flex justify-between items-center mb-2">
            <div className="font-semibold text-xs text-slate-700">
              {model ?? 'SX1278'}{' '}
              <span className="font-mono">
                {frequency != null ? `${frequency} MHz` : '433 MHz'}
              </span>
            </div>
            {detected ? (
              <span className="flex items-center gap-1 text-[10px] font-bold text-aeris">
                <span className="w-1.5 h-1.5 rounded-full bg-aeris pulse-indicator" /> Detected
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[10px] font-bold text-slate-400">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-300" /> Disconnected
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-slate-400 block text-[9px] uppercase">RSSI</span>
              <span className="font-mono text-slate-800">{na(rssi, ' dBm')}</span>
            </div>
            <div>
              <span className="text-slate-400 block text-[9px] uppercase">SNR</span>
              <span className="font-mono text-slate-800">{na(snr, ' dB')}</span>
            </div>
            <div>
              <span className="text-slate-400 block text-[9px] uppercase">RX Packets</span>
              <span className="font-mono text-slate-800">{rxCount ?? 'N/A'}</span>
            </div>
            <div>
              <span className="text-slate-400 block text-[9px] uppercase">Last RX</span>
              <span className="font-mono text-slate-800">{lastRxLabel}</span>
            </div>
          </div>

          {!detected && (
            <div className="mt-2 text-[10px] text-slate-500 leading-snug">
              {loraHardware?.reason ?? radio?.reason ?? 'No LoRa radio answered on the SPI bus.'}
            </div>
          )}
          {detected && rxCount === null && (
            <div className="mt-2 text-[10px] text-slate-500 leading-snug">
              Listening on {frequency ?? '433'} MHz — no packets received yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
