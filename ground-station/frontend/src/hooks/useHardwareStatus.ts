import { useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import {
  REPORT_STALE_MS,
  deviceConnectionState,
  deviceDisplayState,
  linkStateFrom,
} from '../lib/hardwareStatus';
import type { DeviceHealthMap, DeviceHealthState } from '../types';

export interface HardwareStatusView {
  /** True while the live channel delivers any message (heartbeat included). */
  linkFresh: boolean;
  /** True while a fresh *hardware* report has arrived. */
  hardwareFresh: boolean;
  /** Age of the last hardware report in seconds (null when none arrived). */
  reportAgeS: number | null;
  /** Age of the last message of any kind in seconds (null when none arrived). */
  linkAgeS: number | null;
  /**
   * Per-device health as measured by the backend, with "online but no longer
   * fresh" presented as 'stale' instead of 'offline'.
   */
  devices: DeviceHealthMap;
  /** Raspberry Pi 4B - online while the station reports fresh system data. */
  rpi: DeviceHealthState;
  /** MLX90640 thermal camera - online while it delivers real frames. */
  thermal: DeviceHealthState;
  /** SX1278 LoRa radio - online while it answers on the SPI bus. */
  lora: DeviceHealthState;
  /** I2C bus - online while the bus scan answers. */
  i2c: DeviceHealthState;
  /** SPI bus - online while the device nodes are usable. */
  spi: DeviceHealthState;
}

/**
 * Derives the state of every hardware component from the real reports.
 *
 * A transport problem and a hardware failure are different things:
 *
 *  - `hardwareFresh` (report age) says whether the *measurement* is current,
 *  - `linkFresh` / `linkAgeS` say whether the *channel* is alive,
 *  - a device is only 'offline' when the backend measured it offline,
 *  - a previously-online device whose report stopped arriving is 'stale'
 *    (amber), never a red 'offline'.
 */
export function useHardwareStatus(): HardwareStatusView {
  const {
    devices,
    hardware,
    thermal: thermalStatus,
    system,
    hardwareUpdatedAt,
    lastMessageAt,
  } = useAppStore();
  // A 1 Hz ticker so a report that goes stale is noticed even when no new
  // message arrives (the link is already broken in that case). The current
  // time is kept in state so the derivation stays a pure function of it.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const reportAgeMs = hardwareUpdatedAt != null ? now - hardwareUpdatedAt : null;
  const linkAgeMs = lastMessageAt != null ? now - lastMessageAt : null;
  // The link is alive while ANY message (including the 10 s heartbeat) is
  // recent. Hardware freshness is stricter and only advances on a real report.
  const linkFresh = linkAgeMs != null && linkAgeMs >= 0 && linkAgeMs <= REPORT_STALE_MS;
  const hardwareFresh =
    reportAgeMs != null && reportAgeMs >= 0 && reportAgeMs <= REPORT_STALE_MS;

  // The backend already classifies every device (it owns the hardware
  // watchdog). Its verdict is used when it is present.
  const fromDevices = (id: string): DeviceHealthState | null => devices?.[id]?.state ?? null;

  const fallback = (connected: boolean | undefined | null, timestamp: string | null | undefined) =>
    deviceConnectionState(connected, timestamp, REPORT_STALE_MS, now);

  /** Apply report-age staleness to the backend's measured verdict. */
  const display = (measuredState: DeviceHealthState): DeviceHealthState =>
    deviceDisplayState(measuredState, reportAgeMs);

  /**
   * Prefer the backend's per-device verdict. Fall back to the aggregate
   * hardware report only when that report exists; with no report at all the
   * honest answer is 'unknown', not 'offline'.
   */
  const deviceState = (
    id: string,
    hasReport: boolean,
    fallbackState: () => DeviceHealthState,
  ): DeviceHealthState => {
    const fromMap = fromDevices(id);
    if (fromMap != null) return display(fromMap);
    if (!hasReport) return 'unknown';
    return display(fallbackState());
  };

  const rpi = deviceState('rpi4b', system != null, () => 'online');
  const thermal = deviceState(
    'mlx90640',
    hardware?.mlx90640 !== undefined || thermalStatus != null,
    () =>
      hardware?.mlx90640?.connected && thermalStatus?.ok ? 'online' : 'offline',
  );
  const lora = deviceState(
    'lora_sx1278',
    hardware?.lora_sx1278 !== undefined,
    () => fallback(hardware?.lora_sx1278?.connected, hardware?.timestamp),
  );
  const i2c = deviceState(
    'i2c_bus',
    hardware?.i2c !== undefined,
    () => fallback(hardware?.i2c?.bus_active, hardware?.timestamp),
  );
  const spi = deviceState(
    'spi_bus',
    hardware?.spi !== undefined,
    () => fallback(hardware?.spi?.connected, hardware?.timestamp),
  );

  // Present the same stale-aware verdict in the per-device health list.
  const displayDevices: DeviceHealthMap = Object.fromEntries(
    Object.entries(devices ?? {}).map(([id, device]) => [
      id,
      { ...device, state: deviceDisplayState(device.state, reportAgeMs) },
    ]),
  );

  return {
    linkFresh,
    hardwareFresh,
    reportAgeS: reportAgeMs != null ? Math.round(reportAgeMs / 100) / 10 : null,
    linkAgeS: linkAgeMs != null ? Math.round(linkAgeMs / 100) / 10 : null,
    devices: displayDevices,
    rpi,
    thermal,
    lora,
    i2c,
    spi,
  };
}

/** Link state (connecting / live / stale / offline) for the top banner. */
export function useLinkState() {
  const { linkState, lastMessageAt } = useAppStore();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  // While the socket lifecycle says the link is up, derive live/stale/offline
  // from the age of the last message instead of trusting a stale boolean.
  if (linkState === 'live' || linkState === 'stale') {
    return linkStateFrom(lastMessageAt, now);
  }
  return linkState;
}
