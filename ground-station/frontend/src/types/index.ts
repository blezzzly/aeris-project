/**
 * Flight telemetry as reported by the real hardware.
 *
 * Every numeric field is nullable: it stays null when no real sensor supplied
 * the value, so the dashboard can show N/A / Disconnected instead of a fake
 * reading. `lat`/`lng` stay numeric because the map needs a coordinate pair -
 * `positionLive` tells the map whether that position came from a real GPS.
 */
export interface UAVTelemetry {
  altitude: number | null;
  speed: number | null;
  heading: number | null;
  battery: number | null;
  battery_voltage: number | null;
  lat: number;
  lng: number;
  positionLive: boolean;
  connected: boolean;
  source: string;
  last_update: string | null;
  unavailable_reason: string | null;
}

/** A single hardware sensor and whether it was actually detected. */
export interface SensorInfo {
  available: boolean;
  reason: string | null;
  [key: string]: unknown;
}

/** Per-sensor status reported by the backend. */
export interface SensorStatus {
  lora?: SensorInfo;
  gps?: SensorInfo;
  battery?: SensorInfo;
  barometer?: SensorInfo;
  compass?: SensorInfo;
}

/** Identity of the LoRa radio, read from the hardware's own registers. */
export interface LoraHardware {
  available: boolean;
  reason: string | null;
  model?: string | null;
  frequency_mhz?: number | null;
  bandwidth?: string | null;
  spi_device?: string | null;
  version_register?: string | null;
}

/** Live status of the ground-side LoRa receiver. */
export interface LoraRxStatus {
  hardware_available: boolean;
  running: boolean;
  model: string | null;
  frequency_mhz: number;
  rx_count: number;
  crc_fail: number;
  duplicate_count: number;
  last_rssi: number | null;
  last_snr: number | null;
  /** ISO timestamp of the last successfully decoded packet (null until then). */
  last_rx?: string | null;
  dio0_irq_count?: number;
  rx_errors?: number;
  reason: string | null;
}

/**
 * Link statistics. A LoRa radio that has not received a packet has no
 * meaningful RSSI/SNR/latency, so those stay null and the UI shows N/A.
 */
export interface LinkHealth {
  latency: number | null;
  rssi: number | null;
  bitrate?: number | null;
  plr?: number | null;
}

export interface CommLinks {
  wifi: LinkHealth;
  lora: LinkHealth;
}

export interface SurvivorDetection {
  id: string;
  timestamp: string;
  confidence: number;
  /** Real GPS fix. null when the detection source has no fix (never a fabricated 0,0). */
  lat: number | null;
  lng: number | null;
  altitude: number | null;
  distance: number | null;
  status: 'new' | 'acknowledged';
  /**
   * Where the position came from:
   *   'gps'    - a real position reported by the drone's GPS (map-addressable)
   *   'thermal' - detected by the MLX90640, no position available
   *   'demo'   - seeded sample record, NOT a measured position
   */
  positionSource?: 'gps' | 'thermal' | 'demo';
  /** Hotspot pixel of the MLX90640 frame that produced this detection. */
  hotspot?: { x: number; y: number; c: number } | null;
  imageUrl?: string;
  thermalImageUrl?: string;
}

/** A valid detection fix: finite numbers, and not the (0, 0) null-island. */
export function isValidDetectionFix(lat: number | null | undefined, lng: number | null | undefined): boolean {
  return (
    typeof lat === 'number' && Number.isFinite(lat) &&
    typeof lng === 'number' && Number.isFinite(lng) &&
    !(lat === 0 && lng === 0)
  );
}

export interface Mission {
  id: string;
  name: string;
  startedAt: string;
  endedAt?: string;
  status: 'active' | 'ended';
}

export interface ThermalStatus {
  ok: boolean;
  error: string | null;
  min_c: number | null;
  max_c: number | null;
  mean_c: number | null;
  hotspot_x: number | null;
  hotspot_y: number | null;
  hotspot_c: number | null;
  resolution: string;
  address: string;
  /** Sensor model as reported by the backend (real hardware). */
  model?: string;
  frames: number;
  /** Measured frame rate of the sensor feed (frames/s), null when not running. */
  fps?: number | null;
  human_detected?: boolean;
  /** Latest raw 32x24 (row-major, 768 values) frame, only on /api/thermal/status. */
  matrix?: number[] | null;
}

/**
 * A live MLX90640 frame pushed by the backend over the WebSocket.
 * `matrix` is row-major 24x32 (row = image y, col = image x), raw sensor
 * temperatures in deg C. `matrix: null` means the sensor currently has no
 * usable frame (offline / consecutive read failures).
 */
export interface ThermalFrame {
  matrix: number[] | null;
  min_c: number | null;
  max_c: number | null;
  mean_c: number | null;
  hotspot_x: number | null;
  hotspot_y: number | null;
  hotspot_c: number | null;
  frames: number | null;
  /** Measured frames per second of the sensor feed (real, not assumed). */
  fps?: number | null;
  error?: string | null;
  timestamp?: string | null;
  /** Client-side receive time (epoch ms); not sent by the backend. */
  receivedAt: number;
}

export interface SystemStats {
  cpu_temp_c: number | null;
  cpu_usage_pct: number | null;
  ram_usage_pct: number | null;
  ram_total_mb: number | null;
  ram_used_mb: number | null;
  uptime_s: number | null;
  cpu_count: number | null;
  loadavg: number[] | null;
  timestamp: string;
}

export interface HardwareStatus {
  rpi: { model: string; connected: boolean };
  i2c: {
    bus: number;
    /** true when the I2C bus itself answered the scan */
    bus_active: boolean;
    devices: string[];
    mlx90640_detected: string | boolean;
    connected: boolean;
    error?: string | null;
    scanned_at?: string | null;
  };
  spi: {
    bus: number;
    /** true when the SPI device nodes exist and are accessible */
    bus_active: boolean;
    devices: string[];
    connected: boolean;
    error?: string | null;
    scanned_at?: string | null;
  };
  mlx90640: {
    address: string;
    resolution: string;
    connected: boolean;
    error: string | null;
    /** true when the last frame is too old to be trusted */
    stale?: boolean;
    /** ISO timestamp of the last real frame */
    last_frame?: string | null;
    /** Age of the last real frame in seconds */
    frame_age_s?: number | null;
    frames?: number | null;
  };
  lora_sx1278: {
    model: string | null;
    frequency_mhz: number | null;
    bandwidth: string | null;
    spi_device: string | null;
    spi_connected: boolean;
    connected: boolean;
    transmission_status: string;
    last_tx_start: string | null;
    last_tx_ok: number | null;
    last_tx_error: string | null;
    tx_ok_count: number | null;
    tx_err_count: number | null;
    ack_received: boolean;
    tx_reporter_fresh: boolean;
    rx_packet_count: number | null;
    last_rx: string | null;
    rssi_dbm: number | null;
    snr_db: number | null;
    reason: string | null;
    /** true while the ground-side receiver holds the radio */
    rx_running?: boolean;
    /** ISO timestamp of the last SPI identity probe */
    last_probe?: string | null;
    probe_fresh?: boolean;
  };
  timestamp: string;
}

/**
 * Health of one hardware component, as measured by the backend.
 *
 * `state` is derived from real communication, never assumed:
 *   online  - the device answered recently
 *   stale   - it answered, but not recently enough to trust the reading
 *   offline - it did not answer
 */
export type DeviceHealthState = 'online' | 'stale' | 'offline' | 'unknown';

export interface DeviceHealth {
  label: string;
  state: DeviceHealthState;
  connected: boolean;
  reason: string | null;
  /** ISO timestamp of the last successful communication with the device. */
  last_seen: string | null;
  detail?: string | null;
}

export type DeviceHealthMap = Record<string, DeviceHealth>;

export interface DetectionPacket {
  timestamp: string;
  confidence: number;
  lat: number;
  lng: number;
  altitude: number;
  distance: number;
  frameNumber: number;
}
