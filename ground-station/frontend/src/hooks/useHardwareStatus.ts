import { useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { REPORT_STALE_MS, deviceConnectionState, linkStateFrom } from '../lib/hardwareStatus';
import type { DeviceHealthMap, DeviceHealthState } from '../types';

export interface HardwareStatusView {
  /** True while the ground station keeps delivering a fresh report. */
  linkFresh: boolean;
  /** Age of the last hardware report in seconds (null when none arrived). */
  reportAgeS: number | null;
  /** Per-device health as measured by the backend. */
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
 * Nothing here is assumed: a component is only shown as online when the
 * backend measured it *and* the measurement is recent enough to be trusted.
 * A report that stopped arriving (Wi-Fi drop, backend restart, sensor
 * failure) therefore turns the indicators red instead of leaving a stale
 * green "CONNECTED" on screen.
 */
export function useHardwareStatus(): HardwareStatusView {
  const { devices, hardware, thermal: thermalStatus, system, hardwareUpdatedAt } = useAppStore();
  // A 1 Hz ticker so a report that goes stale is noticed even when no new
  // message arrives (the link is already broken in that case). The current
  // time is kept in state so the derivation stays a pure function of it.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const reportAgeMs = hardwareUpdatedAt != null ? now - hardwareUpdatedAt : null;
  const linkFresh = reportAgeMs != null && reportAgeMs >= 0 && reportAgeMs <= REPORT_STALE_MS;

  // The backend already classifies every device (it owns the hardware
  // watchdog). Its verdict is used when it is present.
  const fromDevices = (id: string): DeviceHealthState | null => devices?.[id]?.state ?? null;

  const fallback = (connected: boolean | undefined | null, timestamp: string | null | undefined) =>
    deviceConnectionState(connected, timestamp, REPORT_STALE_MS, now);

  const rpi: DeviceHealthState = linkFresh
    ? (fromDevices('rpi4b') ?? (system ? 'online' : 'unknown'))
    : 'offline';

  const thermal: DeviceHealthState = linkFresh
    ? (fromDevices('mlx90640') ??
      (hardware?.mlx90640?.connected && thermalStatus?.ok ? 'online' : 'offline'))
    : 'offline';

  const lora: DeviceHealthState = linkFresh
    ? (fromDevices('lora_sx1278') ?? fallback(hardware?.lora_sx1278?.connected, hardware?.timestamp))
    : 'offline';

  const i2c: DeviceHealthState = linkFresh
    ? (fromDevices('i2c_bus') ?? fallback(hardware?.i2c?.bus_active, hardware?.timestamp))
    : 'offline';

  const spi: DeviceHealthState = linkFresh
    ? (fromDevices('spi_bus') ?? fallback(hardware?.spi?.connected, hardware?.timestamp))
    : 'offline';

  return {
    linkFresh,
    reportAgeS: reportAgeMs != null ? Math.round(reportAgeMs / 100) / 10 : null,
    devices: devices ?? {},
    rpi,
    thermal,
    lora,
    i2c,
    spi,
  };
}

/** Link state (connecting / live / stale / offline) for the top banner. */
export function useLinkState() {
  const { linkState, hardwareUpdatedAt } = useAppStore();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (linkState === 'live' && hardwareUpdatedAt != null) {
    return linkStateFrom(hardwareUpdatedAt, now);
  }
  return linkState;
}
