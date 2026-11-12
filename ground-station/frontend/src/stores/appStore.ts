import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  UAVTelemetry,
  CommLinks,
  SurvivorDetection,
  Mission,
  ThermalStatus,
  SystemStats,
  HardwareStatus,
  SensorStatus,
  LoraHardware,
  LoraRxStatus,
  ThermalFrame,
  DeviceHealthMap,
} from '../types';
import type { LinkState } from '../lib/hardwareStatus';

export interface Settings {
  comm: {
    wifiSSID: string;
    wifiBandwidth: number;
    loraFrequency: number;
    loraSpreadingFactor: number;
    videoBitrate: number;
    autoReconnect: boolean;
  };
  mission: {
    autoAcknowledge: boolean;
    minConfidence: number;
    detectionCooldown: number;
    homeLat: number;
    homeLng: number;
    geofenceRadius: number;
  };
  alerts: {
    lowBatteryThreshold: number;
    lowSignalThreshold: number;
    maxAltitude: number;
    soundAlerts: boolean;
    popupsEnabled: boolean;
  };
}

interface AppState {
  activeTab: 'dashboard' | 'livefeed' | 'detections' | 'map' | 'settings';
  sidebarOpen: boolean;
  modalOpen: boolean;
  alertAcknowledged: boolean;
  missionTimeSeconds: number;

  uav: UAVTelemetry;
  survivor: { lat: number; lng: number };
  home: { lat: number; lng: number };

  link: CommLinks;

  detections: SurvivorDetection[];
  mission: Mission;

  environment: {
    temperature: number | null;
    condition: string;
    updatedAt?: string | null;
    source?: string;
  };

  settings: Settings;
  updateSettings: (section: keyof Settings, data: Partial<Settings[keyof Settings]>) => void;
  resetSettings: () => void;

  thermal: ThermalStatus | null;
  thermalFrame: ThermalFrame | null;
  system: SystemStats | null;
  hardware: HardwareStatus | null;
  sensors: SensorStatus;
  loraHardware: LoraHardware | null;
  loraRx: LoraRxStatus | null;
  /** Per-device health reported by the backend (online / stale / offline). */
  devices: DeviceHealthMap;
  /** Client-side timestamp of the last hardware report (epoch ms). */
  hardwareUpdatedAt: number | null;
  /** State of the live link between the dashboard and the ground station. */
  linkState: LinkState;
  setHardwareData: (data: {
    system?: SystemStats | null;
    hardware?: HardwareStatus | null;
    thermal?: ThermalStatus | null;
    sensors?: SensorStatus;
    loraHardware?: LoraHardware | null;
  }) => void;
  setDevices: (devices: DeviceHealthMap) => void;
  setLinkState: (state: LinkState) => void;
  /** Drop the cached hardware state (used when the live link is lost). */
  clearHardwareData: () => void;
  setThermalFrame: (frame: ThermalFrame | null) => void;
  setLoraRx: (status: LoraRxStatus | null) => void;
  setTelemetry: (data: Partial<UAVTelemetry>) => void;

  endMission: () => void;
  startMission: (name: string) => void;
  updateEnvironment: (data: Partial<{ temperature: number | null; condition: string; updatedAt?: string | null; source?: string }>) => void;

  setActiveTab: (tab: AppState['activeTab']) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setModalOpen: (open: boolean) => void;
  acknowledgeAlert: () => void;
  incrementMissionTime: () => void;
  updateUAV: (data: Partial<UAVTelemetry>) => void;
  updateLink: (type: 'wifi' | 'lora', data: Partial<CommLinks['wifi']>) => void;
  addDetection: (detection: SurvivorDetection) => void;
}

const defaultSettings: Settings = {
  comm: {
    wifiSSID: 'AERIS-GroundStation',
    wifiBandwidth: 5,
    loraFrequency: 433,
    loraSpreadingFactor: 7,
    videoBitrate: 5.4,
    autoReconnect: true,
  },
  mission: {
    autoAcknowledge: true,
    minConfidence: 70,
    detectionCooldown: 5,
    homeLat: 14.2485,
    homeLng: 120.7290,
    geofenceRadius: 500,
  },
  alerts: {
    lowBatteryThreshold: 30,
    lowSignalThreshold: -80,
    maxAltitude: 150,
    soundAlerts: true,
    popupsEnabled: true,
  },
};

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      activeTab: 'dashboard',
      sidebarOpen: false,
      modalOpen: false,
      alertAcknowledged: false,
      missionTimeSeconds: 5077,

      // Flight telemetry starts empty: it is filled exclusively from the real
      // hardware reports delivered by the backend WebSocket.
      uav: {
        altitude: null,
        speed: null,
        heading: null,
        battery: null,
        battery_voltage: null,
        lat: 14.2500,
        lng: 120.7300,
        positionLive: false,
        connected: false,
        source: 'none',
        last_update: null,
        unavailable_reason: 'Waiting for the first telemetry report from the hardware',
      },
      survivor: {
        lat: 14.2515,
        lng: 120.7310,
      },
      home: {
        lat: 14.2485,
        lng: 120.7290,
      },

      // Link statistics are only filled once a real link measurement exists.
      link: {
        wifi: { latency: null, rssi: null, bitrate: null },
        lora: { latency: null, rssi: null, plr: null },
      },

      // Seeded sample records. They are flagged 'demo' so the map never
      // presents them as measured positions.
      detections: [
        {
          id: 'ALERT-2026-08-27-102436',
          timestamp: '10:24:36 AM',
          confidence: 92,
          lat: 14.2221,
          lng: 120.9345,
          altitude: 120,
          distance: 120,
          status: 'new',
          positionSource: 'demo',
        },
        {
          id: 'ALERT-2026-08-27-094211',
          timestamp: '09:42:11 AM',
          confidence: 89,
          lat: 14.2208,
          lng: 120.9321,
          altitude: 110,
          distance: 210,
          status: 'acknowledged',
          positionSource: 'demo',
        },
      ],

      mission: {
        id: 'mission-001',
        name: 'Maragondon, Cavite Search',
        startedAt: '07:30 AM',
        status: 'active',
      },

      environment: {
        temperature: 28,
        condition: 'Partly Cloudy',
        updatedAt: null,
        source: 'Open-Meteo (live)',
      },

      thermal: null,
      thermalFrame: null,
      system: null,
      hardware: null,
      sensors: {},
      loraHardware: null,
      loraRx: null,
      devices: {},
      hardwareUpdatedAt: null,
      linkState: 'connecting',

      settings: defaultSettings,

      updateSettings: (section, data) =>
        set((s) => ({
          settings: {
            ...s.settings,
            [section]: { ...s.settings[section], ...data },
          },
        })),

      resetSettings: () => set({ settings: defaultSettings }),

      // Only real hardware reports are stored. A field that is absent from the
      // payload keeps its previous value; a null in the payload clears it.
      setHardwareData: (data) =>
        set((s) => {
          const thermal = data.thermal !== undefined ? data.thermal : s.thermal;
          // Seed the canvas from the REST payload's raw matrix when the
          // WebSocket frame stream hasn't delivered one yet (first load).
          let thermalFrame = s.thermalFrame;
          const m = data.thermal?.matrix;
          if (Array.isArray(m) && m.length === 768) {
            const t = data.thermal!;
            if (!thermalFrame || (t.frames ?? 0) >= (thermalFrame.frames ?? 0)) {
              thermalFrame = {
                matrix: m,
                min_c: t.min_c,
                max_c: t.max_c,
                mean_c: t.mean_c,
                hotspot_x: t.hotspot_x,
                hotspot_y: t.hotspot_y,
                hotspot_c: t.hotspot_c,
                frames: t.frames ?? null,
                fps: t.fps ?? null,
                timestamp: null,
                receivedAt: Date.now(),
              };
            }
          }
          return {
            thermal,
            thermalFrame,
            system: data.system !== undefined ? data.system : s.system,
            hardware: data.hardware !== undefined ? data.hardware : s.hardware,
            sensors: data.sensors !== undefined ? data.sensors : s.sensors,
            loraHardware: data.loraHardware !== undefined ? data.loraHardware : s.loraHardware,
            // Remember when this report arrived so the UI can tell a fresh
            // measurement from a stale one.
            hardwareUpdatedAt: Date.now(),
          };
        }),

      setDevices: (devices) => set({ devices }),

      setLinkState: (linkState) => set({ linkState }),

      // When the live link is lost the cached hardware state must not be
      // displayed any more: those values are no longer backed by a real
      // measurement and would be shown as a stale "connected".
      clearHardwareData: () =>
        set({
          thermal: null,
          thermalFrame: null,
          system: null,
          hardware: null,
          sensors: {},
          loraHardware: null,
          loraRx: null,
          devices: {},
          hardwareUpdatedAt: null,
        }),

      setLoraRx: (status) => set({ loraRx: status }),

      setThermalFrame: (frame) => set({ thermalFrame: frame }),

      setTelemetry: (data) =>
        set((s) => {
          const next = { ...s.uav };
          let changed = false;
          const apply = <K extends keyof UAVTelemetry>(key: K, value: UAVTelemetry[K] | undefined) => {
            if (value !== undefined && next[key] !== value) {
              (next[key] as UAVTelemetry[K]) = value;
              changed = true;
            }
          };
          apply('altitude', data.altitude);
          apply('speed', data.speed);
          apply('heading', data.heading);
          apply('battery', data.battery);
          apply('battery_voltage', data.battery_voltage);
          apply('lat', data.lat);
          apply('lng', data.lng);
          apply('positionLive', data.positionLive);
          apply('connected', data.connected);
          apply('source', data.source);
          apply('last_update', data.last_update);
          apply('unavailable_reason', data.unavailable_reason);
          return changed ? { uav: next } : s;
        }),

      endMission: () => {
        const now = new Date();
        const endedAt = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        set((s) => ({
          mission: {
            ...s.mission,
            status: 'ended',
            endedAt,
          },
        }));
      },

      startMission: (name: string) => {
        const now = new Date();
        const startedAt = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        set((s) => ({
          mission: {
            ...s.mission,
            id: `mission-${now.getTime()}`,
            name,
            startedAt,
            status: 'active',
          },
        }));
      },

      updateEnvironment: (data) =>
        set((s) => ({ environment: { ...s.environment, ...data } })),

      setActiveTab: (tab) => set({ activeTab: tab }),
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      setModalOpen: (open) => set({ modalOpen: open }),
      acknowledgeAlert: () => set({ alertAcknowledged: true }),
      incrementMissionTime: () => set((s) => ({ missionTimeSeconds: s.missionTimeSeconds + 1 })),

      updateUAV: (data) =>
        set((s) => {
          const nextUav = { ...s.uav };
          if (data.altitude !== undefined) nextUav.altitude = data.altitude;
          if (data.speed !== undefined) nextUav.speed = data.speed;
          if (data.heading !== undefined) nextUav.heading = data.heading;
          if (data.battery !== undefined) nextUav.battery = data.battery;
          if (data.battery_voltage !== undefined) nextUav.battery_voltage = data.battery_voltage;
          if (data.lat !== undefined) nextUav.lat = data.lat;
          if (data.lng !== undefined) nextUav.lng = data.lng;
          return { uav: nextUav };
        }),
      updateLink: (type, data) =>
        set((s) => ({ link: { ...s.link, [type]: { ...s.link[type], ...data } } })),
      addDetection: (detection) => set((s) => ({ detections: [detection, ...s.detections] })),
    }),
    {
      name: 'aeris-settings',
      partialize: (state) => ({ settings: state.settings }),
    }
  )
);
