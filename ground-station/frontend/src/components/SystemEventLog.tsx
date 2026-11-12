import { useState, useEffect } from 'react';
import { useAppStore } from '../stores/appStore';

// REPLACES DetectionsTable position (lg:col-span-9 in bottom row): System Event Log and Diagnostics.
// Data: real hardware state from WS + REST only — no simulated log entries.
export function SystemEventLog() {
  const { system, hardware, thermal } = useAppStore();
  const [events, setEvents] = useState<{ time: string; msg: string }[]>([]);

  useEffect(() => {
    const ts = new Date().toLocaleTimeString();
    const push = (msg: string) => setEvents((prev) => [{ time: ts, msg }, ...prev].slice(0, 30));
    if (!system && !hardware && !thermal) return;
    if (hardware) {
      push(hardware.mlx90640.connected ? 'MLX90640 @0x33 connected' : `MLX90640 @0x33 ${hardware.mlx90640.error ? `error: ${hardware.mlx90640.error}` : 'not detected'}`);
      push(hardware.spi.connected ? 'SPI0 present (/dev/spidev0.0)' : 'SPI0 absent');
      push(hardware.lora_sx1278.connected ? 'LoRa RX decoding' : `LoRa: ${hardware.lora_sx1278.transmission_status} (no RX decoder)`);
    }
    if (system?.cpu_temp_c != null && system.cpu_temp_c > 80) push(`CPU temperature high: ${system.cpu_temp_c}°C`);
    if (thermal && !thermal.ok && thermal.error) push(`Thermal capture error: ${thermal.error}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [system?.timestamp, hardware?.timestamp, thermal?.frames]);

  return (
    <div className="lg:col-span-9 panel-card overflow-hidden flex flex-col">
      <div className="flex justify-between items-center mb-4 shrink-0 p-4 pb-0">
        <h3 className="text-sm font-bold text-slate-800 uppercase tracking-wider">System Event Log &amp; Diagnostics</h3>
        <span className="text-[10px] text-slate-400">live hardware state</span>
      </div>
      <div className="flex-1 overflow-auto px-4 pb-4">
        {events.length === 0 ? (
          <div className="text-center text-slate-400 text-sm py-10">
            Waiting for hardware state…
          </div>
        ) : (
          <table className="w-full text-left text-xs whitespace-nowrap">
            <thead className="text-[10px] text-slate-400 uppercase tracking-wider sticky top-0 bg-white z-10 shadow-sm">
              <tr><th className="py-2 px-3 font-semibold">Time</th><th className="py-2 px-3 font-semibold">Event</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {events.map((e, i) => (
                <tr key={i}><td className="py-2 px-3 font-mono text-slate-500">{e.time}</td><td className="py-2 px-3">{e.msg}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
