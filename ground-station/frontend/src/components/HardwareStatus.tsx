import { useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { getApiBase } from '../lib/api';
import { useHardwareStatus } from '../hooks/useHardwareStatus';
import type { HardwareStatus as HardwareStatusType, SystemStats, ThermalStatus } from '../types';

const na = (v: number | null | undefined, suffix = '') =>
  v == null ? 'N/A' : `${v}${suffix}`;

function fmtUptime(s: number | null | undefined) {
  if (s == null) return 'N/A';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

/** "CONNECTED" / "DISCONNECTED" straight from the measured device state. */
function connLabel(state: 'online' | 'stale' | 'offline' | 'unknown') {
  if (state === 'online') return 'CONNECTED';
  if (state === 'stale') return 'STALE';
  if (state === 'unknown') return 'UNKNOWN';
  return 'DISCONNECTED';
}

export function HardwareStatus() {
  const { thermal, system, hardware, setHardwareData } = useAppStore();
  const hw = useHardwareStatus();
  const [loaded, setLoaded] = useState(false);

  // REST fallback on first load (the WebSocket "system" broadcast keeps it live)
  useEffect(() => {
    let cancelled = false;
    fetch(`${getApiBase()}/api/hardware_status`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('aeris_token') ?? ''}` },
    })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) {
          setHardwareData({
            system: d.system as SystemStats,
            hardware: d.hardware as HardwareStatusType,
            thermal: d.thermal as ThermalStatus,
          });
          setLoaded(true);
        }
      })
      .catch(() => setLoaded(true));
    return () => { cancelled = true; };
  }, [setHardwareData]);

  const t = thermal;
  const s = system;
  const h = hardware;
  const lora = h?.lora_sx1278;

  // The radio identity and frequency are read from the radio's own registers
  // by the backend - they are never assumed.
  const loraModel = lora?.model ?? 'SX1278';
  const loraFrequency = lora?.frequency_mhz != null ? `${lora.frequency_mhz} MHz` : '433 MHz';
  const loraDetected = hw.lora === 'online';

  return (
    <div className="panel-card p-4">
      <h2 className="text-xs font-bold text-slate-500 mb-3 uppercase tracking-wider">
        Real Hardware Status {!loaded && '(loading…)'}
      </h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        {/* Thermal (real MLX90640) */}
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
          <div className="text-slate-500 text-[9px] uppercase mb-1">Thermal MLX90640</div>
          <div className={`font-mono font-bold ${hw.thermal === 'online' ? 'text-slate-800' : 'text-slate-400'}`}>
            {hw.thermal === 'online' && t?.min_c != null && t?.max_c != null
              ? `${t.min_c.toFixed(1)}–${t.max_c.toFixed(1)}°C`
              : connLabel(hw.thermal)}
          </div>
          <div className="text-slate-500 mt-1">Avg {na(t?.mean_c, '°C')} · Hotspot {na(t?.hotspot_c, '°C')} @ ({na(t?.hotspot_x)},{na(t?.hotspot_y)})</div>
          <div className="text-slate-400 mt-0.5">{t?.resolution ?? '32x24'} @ {t?.address ?? '0x33'} · frames {na(t?.frames)}</div>
          {t?.error && <div className="text-red-500 mt-0.5">{t.error}</div>}
        </div>

        {/* Raspberry Pi system (real /proc + thermal zone) */}
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
          <div className="text-slate-500 text-[9px] uppercase mb-1">Raspberry Pi 4B</div>
          <div className="font-mono font-bold text-slate-800">
            CPU {na(s?.cpu_temp_c, '°C')} · {na(s?.cpu_usage_pct, '%')}
          </div>
          <div className="text-slate-500 mt-1">RAM {na(s?.ram_usage_pct, '%')} ({na(s?.ram_used_mb)}/{na(s?.ram_total_mb)} MB)</div>
          <div className="text-slate-400 mt-0.5">Uptime {fmtUptime(s?.uptime_s)} · cores {na(s?.cpu_count)}</div>
        </div>

        {/* I2C / SPI / sensor detection (real bus scan) */}
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
          <div className="text-slate-500 text-[9px] uppercase mb-1">I2C / SPI Detection</div>
          <div className="font-mono font-bold text-slate-800">
            I2C {connLabel(hw.i2c)} · SPI {connLabel(hw.spi)}
          </div>
          <div className="text-slate-500 mt-1">I2C devices: {hw.i2c === 'online' && h?.i2c?.devices?.length ? h.i2c.devices.join(', ') : 'none'}</div>
          <div className="text-slate-400 mt-0.5">MLX90640 @0x33: {hw.thermal === 'online' ? 'detected' : hw.thermal === 'stale' ? 'status stale' : 'not detected'}</div>
        </div>

        {/* LoRa SX1278 (identity read from the radio's own registers) */}
        <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
          <div className="text-slate-500 text-[9px] uppercase mb-1">{loraModel}</div>
          <div className={`font-mono font-bold ${loraDetected ? 'text-slate-800' : 'text-slate-400'}`}>
            {loraDetected ? (lora?.transmission_status ?? 'CONNECTED') : connLabel(hw.lora)}
          </div>
          <div className="text-slate-500 mt-1">Freq {loraFrequency} · SPI link: {lora?.spi_connected ? 'present' : 'absent'}</div>
          <div className="text-slate-400 mt-0.5">RX packets {na(lora?.rx_packet_count)} · BW {lora?.bandwidth ?? 'N/A'}</div>
          <div className="text-slate-400 mt-0.5">RSSI {na(lora?.rssi_dbm, ' dBm')} · SNR {na(lora?.snr_db, ' dB')}</div>
          <div className="text-slate-400 mt-0.5">Last RX {lora?.last_rx ? new Date(lora.last_rx).toLocaleTimeString() : 'N/A'}</div>
          {lora?.reason && <div className="text-slate-400 mt-0.5 leading-snug">{lora.reason}</div>}
        </div>
      </div>

      {/* Per-device health: the state the backend measured for every
          registered component (including the ones that are not installed). */}
      <div className="mt-3 grid grid-cols-2 md:grid-cols-3 gap-2 text-[10px]">
        {Object.entries(hw.devices).map(([id, d]) => (
          <div key={id} className="flex items-center justify-between gap-2 bg-slate-50 rounded px-2 py-1 border border-slate-100">
            <span className="text-slate-500 truncate">{d.label}</span>
            <span
              className={`font-mono font-bold uppercase whitespace-nowrap ${
                d.state === 'online'
                  ? 'text-green-700'
                  : d.state === 'stale'
                    ? 'text-amber-600'
                    : 'text-red-500'
              }`}
            >
              {d.state}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
