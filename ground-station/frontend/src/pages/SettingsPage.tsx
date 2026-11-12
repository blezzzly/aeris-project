import { useState, useEffect } from 'react';
import { useAppStore } from '../stores/appStore';
import { Icon } from '../components/Icon';
import { getWebSocketUrl } from '../lib/api';

export function SettingsPage() {
  type SectionId = 'comm' | 'mission' | 'alerts' | 'system';
  const { settings, updateSettings, resetSettings, uav, link, hardware } = useAppStore();

  // Real LoRa reception measurements - null until a packet was received.
  const loraRssi = hardware?.lora_sx1278?.rssi_dbm ?? null;
  const loraSnr = hardware?.lora_sx1278?.snr_db ?? null;
  // The frequency the connected radio reports from its own registers.
  const loraFrequency = hardware?.lora_sx1278?.frequency_mhz ?? null;
  const [activeSection, setActiveSection] = useState<SectionId>('comm');
  const [hasChanges, setHasChanges] = useState(false);
  const [saved, setSaved] = useState(false);

  const sections = [
    { id: 'comm' as const, label: 'Comm Links', icon: 'broadcast' as const, hasSettings: true },
    { id: 'mission' as const, label: 'Mission', icon: 'map-trifold' as const, hasSettings: true },
    { id: 'alerts' as const, label: 'Alerts & Thresholds', icon: 'warning-circle' as const, hasSettings: true },
    { id: 'system' as const, label: 'System', icon: 'gear' as const, hasSettings: false },
  ];

  useEffect(() => {
    setHasChanges(false);
    setSaved(false);
  }, [activeSection]);

  const handleChange = () => {
    setHasChanges(true);
    setSaved(false);
  };

  const handleSave = () => {
    setSaved(true);
    setHasChanges(false);
  };

  const handleDiscard = () => {
    setHasChanges(false);
    setSaved(false);
  };

  const renderCommSettings = () => (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-bold text-slate-800 mb-3">5GHz Wi-Fi Video Link</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">SSID</label>
            <input
              type="text"
              value={settings.comm.wifiSSID}
              onChange={(e) => { updateSettings('comm', { wifiSSID: e.target.value }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Bandwidth (MHz)</label>
            <select
              value={settings.comm.wifiBandwidth}
              onChange={(e) => { updateSettings('comm', { wifiBandwidth: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            >
              <option value={20}>20 MHz</option>
              <option value={40}>40 MHz</option>
              <option value={80}>80 MHz</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Video Bitrate (Mbps)</label>
            <input
              type="number"
              step="0.1"
              value={settings.comm.videoBitrate}
              onChange={(e) => { updateSettings('comm', { videoBitrate: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div className="flex items-center">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.comm.autoReconnect}
                onChange={(e) => { updateSettings('comm', { autoReconnect: e.target.checked }); handleChange(); }}
                className="w-4 h-4 text-aeris focus:ring-aeris rounded"
              />
              <span className="text-sm text-slate-700">Auto-reconnect on link loss</span>
            </label>
          </div>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">433 MHz LoRa Telemetry Link (SX1278)</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">
              Frequency (MHz)
              {loraFrequency != null && (
                <span className="ml-2 text-aeris normal-case">radio reports {loraFrequency} MHz</span>
              )}
            </label>
            <select
              value={settings.comm.loraFrequency}
              onChange={(e) => { updateSettings('comm', { loraFrequency: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            >
              <option value={433}>433 MHz (SX1278, EU/ASIA)</option>
              <option value={868}>868 MHz (EU)</option>
              <option value={915}>915 MHz (US/Asia)</option>
              <option value={923}>923 MHz (Asia)</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Spreading Factor</label>
            <select
              value={settings.comm.loraSpreadingFactor}
              onChange={(e) => { updateSettings('comm', { loraSpreadingFactor: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            >
              {[6, 7, 8, 9, 10, 11, 12].map(sf => (
                <option key={sf} value={sf}>SF{sf}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">Current Link Status</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="text-slate-500 text-[9px] uppercase">WiFi Latency</div>
            <div className="font-mono font-bold text-slate-800">
              {link.wifi.latency != null ? Math.round(link.wifi.latency) + 'ms' : 'N/A'}
            </div>
          </div>
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="text-slate-500 text-[9px] uppercase">WiFi RSSI</div>
            <div className="font-mono font-bold text-slate-800">
              {link.wifi.rssi != null ? Math.round(link.wifi.rssi) + 'dBm' : 'N/A'}
            </div>
          </div>
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="text-slate-500 text-[9px] uppercase">LoRa RSSI</div>
            <div className="font-mono font-bold text-slate-800">
              {loraRssi != null ? Math.round(loraRssi) + 'dBm' : 'N/A'}
            </div>
          </div>
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="text-slate-500 text-[9px] uppercase">LoRa SNR</div>
            <div className="font-mono font-bold text-slate-800">
              {loraSnr != null ? loraSnr.toFixed(1) + 'dB' : 'N/A'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const renderMissionSettings = () => (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-bold text-slate-800 mb-3">Detection Settings</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Minimum Confidence (%)</label>
            <input
              type="number"
              min="0"
              max="100"
              value={settings.mission.minConfidence}
              onChange={(e) => { updateSettings('mission', { minConfidence: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Detection Cooldown (s)</label>
            <input
              type="number"
              min="0"
              value={settings.mission.detectionCooldown}
              onChange={(e) => { updateSettings('mission', { detectionCooldown: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div className="md:col-span-2 flex items-center">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.mission.autoAcknowledge}
                onChange={(e) => { updateSettings('mission', { autoAcknowledge: e.target.checked }); handleChange(); }}
                className="w-4 h-4 text-aeris focus:ring-aeris rounded"
              />
              <span className="text-sm text-slate-700">Auto-acknowledge low-confidence detections</span>
            </label>
          </div>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">Home / Base Location</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Home Latitude</label>
            <input
              type="number"
              step="0.0001"
              value={settings.mission.homeLat}
              onChange={(e) => { updateSettings('mission', { homeLat: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Home Longitude</label>
            <input
              type="number"
              step="0.0001"
              value={settings.mission.homeLng}
              onChange={(e) => { updateSettings('mission', { homeLng: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Geofence Radius (m)</label>
            <input
              type="number"
              value={settings.mission.geofenceRadius}
              onChange={(e) => { updateSettings('mission', { geofenceRadius: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
        </div>
      </div>
    </div>
  );

  const renderAlertSettings = () => (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-bold text-slate-800 mb-3">Thresholds</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Low Battery (%)</label>
            <input
              type="number"
              min="0"
              max="100"
              value={settings.alerts.lowBatteryThreshold}
              onChange={(e) => { updateSettings('alerts', { lowBatteryThreshold: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Low Signal (dBm)</label>
            <input
              type="number"
              value={settings.alerts.lowSignalThreshold}
              onChange={(e) => { updateSettings('alerts', { lowSignalThreshold: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-slate-500 block mb-1">Max Altitude (m)</label>
            <input
              type="number"
              value={settings.alerts.maxAltitude}
              onChange={(e) => { updateSettings('alerts', { maxAltitude: Number(e.target.value) }); handleChange(); }}
              className="w-full text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-aeris"
            />
          </div>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">Notifications</h3>
        <div className="space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.alerts.soundAlerts}
              onChange={(e) => { updateSettings('alerts', { soundAlerts: e.target.checked }); handleChange(); }}
              className="w-4 h-4 text-aeris focus:ring-aeris rounded"
            />
            <span className="text-sm text-slate-700">Enable sound alerts</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.alerts.popupsEnabled}
              onChange={(e) => { updateSettings('alerts', { popupsEnabled: e.target.checked }); handleChange(); }}
              className="w-4 h-4 text-aeris focus:ring-aeris rounded"
            />
            <span className="text-sm text-slate-700">Show popup on detection</span>
          </label>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">Current Status</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className={`rounded-lg p-3 ${uav.battery != null && uav.battery < settings.alerts.lowBatteryThreshold ? 'bg-red-50 border border-red-200' : 'bg-slate-50'}`}>
            <div className="text-slate-500 text-[9px] uppercase">Battery</div>
            <div className="font-mono font-bold text-slate-800">{Math.round(uav.battery ?? 0)}%</div>
          </div>
          <div className={`rounded-lg p-3 ${link.wifi.rssi != null && link.wifi.rssi < settings.alerts.lowSignalThreshold ? 'bg-red-50 border border-red-200' : 'bg-slate-50'}`}>
            <div className="text-slate-500 text-[9px] uppercase">Signal</div>
            <div className="font-mono font-bold text-slate-800">{Math.round(link.wifi.rssi ?? 0)}dBm</div>
          </div>
          <div className={`rounded-lg p-3 ${uav.altitude != null && uav.altitude > settings.alerts.maxAltitude ? 'bg-red-50 border border-red-200' : 'bg-slate-50'}`}>
            <div className="text-slate-500 text-[9px] uppercase">Altitude</div>
            <div className="font-mono font-bold text-slate-800">{uav.altitude != null ? `${uav.altitude.toFixed(1)}m` : 'N/A'}</div>
          </div>
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="text-slate-500 text-[9px] uppercase">GPS</div>
            <div className="font-mono font-bold text-aeris text-[10px]">LOCK</div>
          </div>
        </div>
      </div>
    </div>
  );

  const renderSystemSettings = () => (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-bold text-slate-800 mb-3">System Information</h3>
        <div className="bg-slate-50 rounded-lg p-4 text-xs space-y-2">
          <div className="flex justify-between">
            <span className="text-slate-500">Application Version:</span>
            <span className="font-mono font-semibold text-slate-800">AERIS v1.0.0</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Build Date:</span>
            <span className="font-mono text-slate-800">2026-09-04</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Backend URL:</span>
            <span className="font-mono text-slate-800">{getWebSocketUrl()}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Device:</span>
            <span className="font-mono text-slate-800">AERIS UAV-01</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Settings Storage:</span>
            <span className="font-mono text-aeris">LocalStorage</span>
          </div>
        </div>
      </div>

      <div className="border-t border-slate-100 pt-6">
        <h3 className="text-sm font-bold text-slate-800 mb-3">Actions</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <button className="bg-slate-50 hover:bg-slate-100 text-slate-700 text-sm font-semibold py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2">
            <Icon name="arrow-right" /> Export Detection Logs
          </button>
          <button className="bg-slate-50 hover:bg-slate-100 text-slate-700 text-sm font-semibold py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2">
            <Icon name="info" /> View System Diagnostics
          </button>
          <button 
            onClick={() => { useAppStore.getState().updateUAV({ battery: 78, altitude: 120 }); }}
            className="bg-amber-50 hover:bg-amber-100 text-amber-700 text-sm font-semibold py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <Icon name="warning-circle" /> Reset Connection
          </button>
          <button
            onClick={() => { resetSettings(); handleChange(); }}
            className="bg-red-50 hover:bg-red-100 text-danger text-sm font-semibold py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <Icon name="warning-circle" /> Factory Reset
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="h-full">
      <div className="panel-card min-h-full flex flex-col">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6 border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-xl font-bold text-slate-800 uppercase">Settings</h2>
            <p className="text-xs text-slate-500 mt-1">Configure AERIS ground station parameters</p>
          </div>
          <div className="flex items-center gap-2">
            {hasChanges && (
              <span className="text-[10px] text-amber-600 bg-amber-50 px-2 py-1 rounded">Unsaved changes</span>
            )}
            {saved && !hasChanges && (
              <span className="text-[10px] text-emerald-600 bg-emerald-50 px-2 py-1 rounded">Saved</span>
            )}
          </div>
        </div>

        <div className="flex flex-col lg:flex-row gap-6 flex-1">
          <aside className="lg:w-56 shrink-0">
            <nav className="space-y-1">
              {sections.map((section) => (
                <button
                  key={section.id}
                  onClick={() => setActiveSection(section.id)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 ${
                    activeSection === section.id
                      ? 'bg-aeris-light text-aeris'
                      : 'text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  <Icon name={section.icon} className="text-base" />
                  {section.label}
                </button>
              ))}
            </nav>
          </aside>

          <main className="flex-1 min-w-0">
          {activeSection === 'comm' && renderCommSettings()}
          {activeSection === 'mission' && renderMissionSettings()}
          {activeSection === 'alerts' && renderAlertSettings()}
          {activeSection === 'system' && renderSystemSettings()}

          {(activeSection === 'comm' || activeSection === 'mission' || activeSection === 'alerts') && (
              <div className="mt-6 pt-6 border-t border-slate-100 flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={!hasChanges}
                  className={`text-sm font-semibold py-2 px-4 rounded-lg transition-colors ${
                    hasChanges
                      ? 'bg-aeris hover:bg-aeris-dark text-white'
                      : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                  }`}
                >
                  Save Changes
                </button>
                <button
                  onClick={handleDiscard}
                  disabled={!hasChanges}
                  className={`text-sm font-semibold py-2 px-4 rounded-lg transition-colors ${
                    hasChanges
                      ? 'bg-slate-100 hover:bg-slate-200 text-slate-700'
                      : 'bg-slate-50 text-slate-300 cursor-not-allowed'
                  }`}
                >
                  Discard
                </button>
              </div>
          )}
          </main>
        </div>
      </div>
    </div>
  );
}
