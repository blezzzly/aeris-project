"""
sensors.py - Real hardware telemetry readers for the AERIS Ground Station.

Every reader in this module talks to ACTUAL connected hardware and reports
exactly what it found. Nothing is simulated and nothing is invented:

  * a reader returns ``available=True`` only when a real device answered
  * a reader that finds no device returns ``available=False`` plus a human
    readable ``reason``
  * measurements that the hardware cannot provide are ``None`` (the dashboard
    renders those as N/A / Disconnected)

Supported hardware
------------------
  MLX90640   thermal camera (I2C 0x33)      -> live.py (thermal pipeline)
  SX1278     LoRa radio (SPI 0)             -> probe_sx1278()
  GPS        NMEA receiver (serial)         -> read_gps()
  INA219 /   battery monitor (I2C 0x40)     -> read_battery()
  INA260
  BMP280 /   barometer (I2C 0x76/0x77)      -> read_barometer()
  BMP388
  QMC5883L   magnetometer (I2C 0x0D)        -> read_compass()
"""

import os
import re
import sys
import time
import math
import logging

logger = logging.getLogger(__name__)

# The onboard-drone virtualenv carries the compiled spidev module for this
# platform; reuse it when the backend venv does not provide one.
_DRONE_SITE = "/home/piaeris/aeris-project/drone-onboard/raspberry-pi-4b/venv/lib/python3.13/site-packages"
if os.path.isdir(_DRONE_SITE) and _DRONE_SITE not in sys.path:
    sys.path.append(_DRONE_SITE)

# ── Optional dependencies ──────────────────────────────────────────────────
try:
    import spidev
    _SPIDEV_AVAILABLE = True
except Exception:
    spidev = None
    _SPIDEV_AVAILABLE = False

try:
    import serial
    _SERIAL_AVAILABLE = True
except Exception:
    serial = None
    _SERIAL_AVAILABLE = False

try:
    import board
    import busio
    from adafruit_bus_device.i2c_device import I2CDevice
    _I2C_AVAILABLE = True
except Exception:
    board = busio = I2CDevice = None
    _I2C_AVAILABLE = False


# ── SX127x radio identity ──────────────────────────────────────────────────
# RegVersion (0x42) values of the SX127x family.
_SX127X_VERSIONS = {
    0x12: "SX1276/SX1278",
    0x11: "SX1278",
    0x22: "SX1272",
    0x1B: "SX1273",
    0x10: "SX1273 (rev)",
}
# LoRa bandwidth (RegModemConfig1 bits 7:4)
_LORA_BANDWIDTH = {
    0: "7.8 kHz", 1: "10.4 kHz", 2: "15.6 kHz", 3: "20.8 kHz",
    4: "31.25 kHz", 5: "41.7 kHz", 6: "62.5 kHz",
    7: "125 kHz", 8: "250 kHz", 9: "500 kHz",
}
# Long-range mode configuration target for the 433 MHz band (RegOpMode + RegFrf).
_LORA_FREQ_HZ = 433_000_000
_FRF_TARGET = round(_LORA_FREQ_HZ * (1 << 19) / 32_000_000)  # exact 0x6C4000
# RegOpMode (LoRa, LongRangeMode=1 | LowFrequencyModeOn bit3 for <525 MHz)
_OPMODE_LORA_SLEEP_LF = 0x88
_OPMODE_LORA_STDBY_LF = 0x89
_OPMODE_LORA_TX_LF = 0x8B
_OPMODE_LORA_RXCONT_LF = 0x8D

# Serial ports a GPS module is commonly wired to on a Raspberry Pi.
_GPS_DEVICES = (
    "/dev/ttyAMA0", "/dev/ttyS0", "/dev/ttyUSB0",
    "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0",
)

# Sea-level pressure reference (hPa) used for the barometric altitude.
_SEA_LEVEL_HPA = float(os.environ.get("AERIS_SEA_LEVEL_HPA", "1013.25"))
# Battery pack used for the percentage calculation (LiPo cell count).
_BATTERY_CELLS = float(os.environ.get("AERIS_BATTERY_CELLS", "3"))
# Full-charge voltage of one LiPo cell (V) and the empty-cell voltage (V).
_CELL_FULL_V = 4.2
_CELL_EMPTY_V = 3.5

_i2c_bus = None


# ── Helpers ────────────────────────────────────────────────────────────────
def _get_i2c_bus():
    """Return a shared I2C bus handle (the thermal thread uses the same one)."""
    global _i2c_bus
    if _i2c_bus is None:
        _i2c_bus = busio.I2C(board.SCL, board.SDA, frequency=400000)
    return _i2c_bus


def _unavailable(reason, **extra):
    data = {"available": False, "reason": reason}
    data.update(extra)
    return data


# ── SX1278 LoRa radio ──────────────────────────────────────────────────────
def _sx1278_read_reg(spi, addr):
    """Read a register with the manual GPIO24 chip-select."""
    import sx1278_gpio as _gpio
    with _gpio.RADIO_LOCK:
        _gpio.cs_low()
        spi.xfer2([addr & 0x7F])
        val = spi.xfer2([0x00])[0]
        _gpio.cs_high()
    return val


def _sx1278_write_reg(spi, addr, value):
    """Write a register with the manual GPIO24 chip-select."""
    import sx1278_gpio as _gpio
    with _gpio.RADIO_LOCK:
        _gpio.cs_low()
        spi.xfer2([addr | 0x80, value & 0xFF])
        _gpio.cs_high()


def _sx1278_open_spi(spi_bus=0):
    spi = spidev.SpiDev()
    try:
        spi.open(spi_bus, 0)
    except Exception as exc:
        return None, f"cannot open /dev/spidev{spi_bus}.0: {exc}"
    spi.max_speed_hz = 1_000_000
    spi.mode = 0
    spi.lsbfirst = False
    # The radio is not on the kernel chip-select lines - manual GPIO24 only.
    spi.no_cs = True
    return spi, None


def sx1278_read_fifo(spi, count):
    """Burst-read ``count`` bytes from the FIFO (auto-incrementing address).

    Returns b"" when the FIFO holds no bytes (e.g. an RxTimeout interrupt) -
    spidev rejects an empty transfer list with "Empty argument list".
    """
    import sx1278_gpio as _gpio
    count = int(count)
    if count <= 0:
        return b""
    with _gpio.RADIO_LOCK:
        _gpio.cs_low()
        spi.xfer2([0x00])
        data = spi.xfer2([0x00] * count)
        _gpio.cs_high()
    return bytes(data)


def sx1278_write_fifo(spi, data):
    """Write raw bytes into the FIFO (address must already be set)."""
    import sx1278_gpio as _gpio
    with _gpio.RADIO_LOCK:
        _gpio.cs_low()
        spi.xfer2([0x00 | 0x80] + list(data))
        _gpio.cs_high()


def _sx1278_version_samples(spi, count=8, fsk_sleep=False):
    """Sample RegVersion.

    ``fsk_sleep=True`` first drops the radio into FSK sleep - only needed for
    cold diagnosis. The fast re-probe reads RegVersion directly in whatever
    mode the radio is in, so a live receiver is never disturbed.
    """
    if fsk_sleep:
        _sx1278_write_reg(spi, 0x01, 0x00)
        time.sleep(0.01)
    return [_sx1278_read_reg(spi, 0x42) for _ in range(count)]


def sx1278_init(spi_bus=0, frequency_hz=_LORA_FREQ_HZ):
    """Reset (only when needed) and initialise the SX1278 for 433 MHz LoRa.

    Serialized against every other radio user in this process (sensor poller,
    RX watcher) via sx1278_gpio.RADIO_LOCK.
    """
    import sx1278_gpio as _gpio
    with _gpio.RADIO_LOCK:
        return _sx1278_init_locked(spi_bus, frequency_hz)


def _sx1278_init_locked(spi_bus=0, frequency_hz=_LORA_FREQ_HZ):
    """Reset (only when needed) and initialise the SX1278 for 433 MHz LoRa.

    Strategy: read RegVersion first without touching anything - RegVersion is
    readable in any mode, so a healthy radio is left alone (the 60 s dashboard
    re-probe must not knock a live receiver out of receive mode). If the radio
    does not answer, pulse NRESET and retry the full init.

    Returns (spi_handle, info); ``info`` always reports what was measured.
    """
    import sx1278_gpio as _gpio

    info = {"available": False, "reason": None}
    if not _SPIDEV_AVAILABLE:
        info["reason"] = "spidev module not available for this platform"
        return None, info

    spi, err = _sx1278_open_spi(spi_bus)
    if spi is None:
        info["reason"] = err
        return None, info

    try:
        # Fast path: read RegVersion directly - it is available in every mode,
        # so a running receiver is never disturbed. Fall back to the FSK-sleep
        # read and, if needed, a full reset only when this fails.
        samples = _sx1278_version_samples(spi, fsk_sleep=False)
        cold_start = not _sx1278_version_ok(samples)
        if cold_start:
            samples = _sx1278_version_samples(spi, fsk_sleep=True)
        if not _sx1278_version_ok(samples):
            # Cold init or a dead-looking radio: pulse NRESET and retry.
            try:
                _gpio.reset_pulse()
            except Exception as exc:
                info["reason"] = f"cannot reset the radio (GPIO23): {exc}"
                spi.close()
                return None, info
            time.sleep(0.05)  # let the radio boot out of reset
            samples = _sx1278_version_samples(spi, fsk_sleep=True)

        stable = len(set(samples)) == 1
        version = samples[0]
        if not stable or version not in _SX127X_VERSIONS:
            hint = ""
            if all(s == 0x00 for s in samples):
                hint = (" — MISO (GPIO9) is held LOW: the radio is either unpowered, "
                        "held in reset, not wired, or its MISO does not reach GPIO9. "
                        "Check: 3.3 V (NOT 5 V) on VCC, common GND, SCK→GPIO11, "
                        "MOSI→GPIO10, MISO→GPIO9, NRESET not tied LOW, antenna fitted.")
            info.update({
                "reason": f"version register returned {sorted(set(hex(s) for s in samples))}{hint}",
                "version_samples": [hex(s) for s in samples],
                "spi_device": "userspace chip-select on GPIO24 (/dev/spidev0.0)",
            })
            spi.close()
            return None, info

        # --- Verify / configure 433 MHz LoRa ---
        # Every step below is conditional on the readback: a healthy, already
        # configured radio is left completely alone (the 60 s re-probe must not
        # disturb a live receiver, flip standby/sleep, or clear IRQ flags).
        frf = (_sx1278_read_reg(spi, 0x06) << 16) | (_sx1278_read_reg(spi, 0x07) << 8) | _sx1278_read_reg(spi, 0x08)
        target = round(frequency_hz * (1 << 19) / 32_000_000)
        opmode = _sx1278_read_reg(spi, 0x01)
        pa_config = _sx1278_read_reg(spi, 0x09)
        frf_ok = frf == target
        pa_ok = bool(pa_config & 0x80)          # SX1278 needs PA_BOOST (no RFIO)
        # Standby (0x81/0x89) and continuous RX (0x85/0x8D) are both healthy
        # resting states - the 60 s re-probe must not flip a live receiver.
        mode_ok = (opmode & 0x87) in (0x81, 0x85, 0x89)

        frf_programmed = False
        if not (frf_ok and pa_ok and mode_ok):
            # Bring the radio to LoRa sleep to safely program FRF/PA/OpMode.
            _sx1278_write_reg(spi, 0x01, _OPMODE_LORA_SLEEP_LF)
            time.sleep(0.01)
            if not frf_ok:
                _sx1278_write_reg(spi, 0x06, (target >> 16) & 0xFF)
                _sx1278_write_reg(spi, 0x07, (target >> 8) & 0xFF)
                _sx1278_write_reg(spi, 0x08, target & 0xFF)
                frf_programmed = True
            if not pa_ok:
                # Keep the user's MaxPower bits, select PA_BOOST.
                _sx1278_write_reg(spi, 0x09, pa_config | 0x80)
            # LoRa standby + LF - the resting state for the ground receiver
            _sx1278_write_reg(spi, 0x01, _OPMODE_LORA_STDBY_LF)
            time.sleep(0.01)
            _sx1278_write_reg(spi, 0x12, 0xFF)  # clear stale IRQ flags

        opmode = _sx1278_read_reg(spi, 0x01)
        modem1 = _sx1278_read_reg(spi, 0x1D)   # RegModemConfig1 (BW | CR)
        frf = (_sx1278_read_reg(spi, 0x06) << 16) | (_sx1278_read_reg(spi, 0x07) << 8) | _sx1278_read_reg(spi, 0x08)
        pa_config = _sx1278_read_reg(spi, 0x09)
        freq_hz = frf * (32_000_000.0 / (1 << 19))
        bandwidth = _LORA_BANDWIDTH.get((modem1 >> 4) & 0x0F, "unknown")
        low_freq = bool((opmode >> 3) & 0x01)

        info.update({
            "available": True,
            "reason": None,
            "model": _SX127X_VERSIONS.get(version, "SX127x"),
            "version_register": hex(version),
            "op_mode": hex(opmode),
            "frf_register": hex(frf),
            "frequency_mhz": round(freq_hz / 1e6, 4),
            "frequency_programmed": frf_programmed,
            "bandwidth": bandwidth,
            "low_frequency_mode_on": low_freq,
            "pa_boost": bool(pa_config & 0x80),
            "pa_config": hex(pa_config),
            "nss_gpio": _gpio.NSS,
            "reset_gpio": _gpio.RESET,
            "dio0_gpio": _gpio.DIO0,
            "dio0_level": _gpio.pin_level(_gpio.DIO0),
            "spi_device": "userspace chip-select GPIO24 on /dev/spidev0.0",
        })
        return spi, info
    except Exception as exc:
        try:
            spi.close()
        except Exception:
            pass
        info["reason"] = f"SPI probe failed: {exc}"
        return None, info


def _sx1278_version_ok(samples) -> bool:
    return len(set(samples)) == 1 and samples[0] in _SX127X_VERSIONS


def probe_sx1278(spi_bus=0, spi_dev=0, timeout=0.05):
    """Verify the LoRa radio with the actual AERIS wiring.

    Chip select is GPIO24 (not the kernel CE lines), RESET is GPIO23 and DIO0
    is GPIO18. The radio identity, frequency and mode are all read back from
    its own registers; the radio is also (re)configured for 433 MHz LoRa
    standby - idempotently, every probe.

    ``spi_dev`` is accepted for API compatibility but the chip select is
    fixed to GPIO24 by the wiring; it is reported, not used.
    """
    spi, info = sx1278_init(spi_bus=spi_bus)
    if spi is not None:
        try:
            spi.close()
        except Exception:
            pass
    info = dict(info)
    info["spi_dev_argument_ignored"] = bool(spi_dev)
    if not info.get("available") and info.get("reason") is None:
        info["reason"] = "no LoRa radio answered on SPI"
    return info


# ── GPS (NMEA over serial) ────────────────────────────────────────────────
_NMEA_GGA = re.compile(
    r"\$G[NP]GGA,(?P<utc>\d{6})(?:\.\d+)?,(?P<lat>[+-]?\d+\.\d+),(?P<ns>[NS]),"
    r"(?P<lon>[+-]?\d+\.\d+),(?P<ew>[EW]),(?P<quality>\d+),(?P<sats>\d+),"
    r"(?P<hdop>[\d.]+),(?P<alt>[+-]?[\d.]+),(?P<alt_unit>M),"
)
_NMEA_RMC = re.compile(
    r"\$G[NP]RMC,(?P<utc>\d{6})(?:\.\d+)?,(?P<status>[AV]),"
    r"(?P<lat>[+-]?\d+\.\d+),(?P<ns>[NS]),(?P<lon>[+-]?\d+\.\d+),(?P<ew>[EW]),"
    r"(?P<speed>[\d.]+),(?P<course>[\d.]+)"
)


def _dm_to_degrees(value, hemisphere):
    """Convert NMEA ddmm.mmmm to signed decimal degrees."""
    if not value:
        return None
    try:
        raw = float(value)
    except ValueError:
        return None
    degrees = int(raw / 100)
    minutes = raw - (degrees * 100)
    result = degrees + (minutes / 60.0)
    if hemisphere in ("S", "W"):
        result = -result
    return round(result, 7)


def read_gps(devices=_GPS_DEVICES, read_seconds=1.0):
    """Read a real GPS/NMEA receiver on a serial port.

    Parses GGA (position, altitude, satellites, fix quality) and RMC (speed over
    ground and course over ground). Returns N/A values when no receiver is
    connected or when it has no fix.
    """
    if not _SERIAL_AVAILABLE:
        return _unavailable("pyserial not installed")

    present = [d for d in devices if os.path.exists(d)]
    if not present:
        return _unavailable("no serial device found for a GPS receiver")

    for device in present:
        try:
            with serial.Serial(device, 9600, timeout=0.2) as port:
                deadline = time.time() + read_seconds
                buffer = ""
                while time.time() < deadline:
                    chunk = port.read(256)
                    if chunk:
                        buffer += chunk.decode("ascii", errors="ignore")
                        if "\n$G" in buffer or "\n$" in buffer:
                            break
                port.reset_input_buffer()
        except Exception as exc:
            continue

        gga = _NMEA_GGA.search(buffer)
        rmc = _NMEA_RMC.search(buffer)
        if not gga and not rmc:
            continue

        lat = lon = None
        altitude = None
        sats = None
        quality = None
        if gga:
            lat = _dm_to_degrees(gga.group("lat"), gga.group("ns"))
            lon = _dm_to_degrees(gga.group("lon"), gga.group("ew"))
            try:
                altitude = float(gga.group("alt"))
            except (TypeError, ValueError):
                altitude = None
            sats = int(gga.group("sats"))
            quality = int(gga.group("quality"))

        speed_mps = None
        course = None
        if rmc:
            try:
                knots = float(rmc.group("speed"))
                speed_mps = round(knots * 0.514444, 2)
            except (TypeError, ValueError):
                speed_mps = None
            try:
                course = float(rmc.group("course"))
            except (TypeError, ValueError):
                course = None

        if lat is None or lon is None:
            return _unavailable(
                f"GPS on {device} has no position fix",
                device=device,
                satellites=sats,
                fix_quality=quality,
            )

        return {
            "available": True,
            "reason": None,
            "device": device,
            "lat": lat,
            "lng": lon,
            "altitude_m": altitude,
            "speed_mps": speed_mps,
            "heading_deg": course,
            "satellites": sats,
            "fix_quality": quality,
            "timestamp": time.time(),
        }

    return _unavailable("GPS receiver present but returned no valid NMEA sentence")


# ── Battery monitor (INA219 / INA260) ─────────────────────────────────────
def read_battery():
    """Read a real battery monitor chip on the I2C bus.

    Supports the TI INA260 (detected through its manufacturer id register)
    and the INA219. Returns bus voltage (V), current (mA) and - when the pack
    size is known - the state of charge in percent.
    """
    if not _I2C_AVAILABLE:
        return _unavailable("no I2C support available")

    for address in (0x40, 0x41):
        try:
            bus = _get_i2c_bus()
            device = I2CDevice(bus, address)
        except Exception:
            continue

        try:
            # The INA260 carries a manufacturer id ("TI" = 0x5449) at 0xFE.
            chip = None
            try:
                if device.readU16(0xFE) == 0x5449:
                    chip = "INA260"
            except Exception:
                chip = None

            if chip == "INA260":
                raw_current = _s16(device.readU16(0x01))     # 1.25 mA / LSB
                raw_voltage = device.readU16(0x02)            # 1.25 mV / LSB
                current_ma = raw_current * 1.25
                voltage_v = raw_voltage * 0.00125
            else:
                # INA219: shunt voltage at 0x01 (signed, 10 uV/LSB),
                # bus voltage at 0x02 (13-bit field, 4 mV/LSB).
                raw_shunt = _s16(device.readU16(0x01))
                raw_bus = device.readU16(0x02) >> 3
                shunt_voltage = raw_shunt * 0.00001
                voltage_v = raw_bus * 0.004
                current_ma = (shunt_voltage / 0.1) * 1000.0   # 0.1 ohm shunt
                chip = "INA219"

            # Sanity check: a real battery pack sits inside a plausible range.
            if voltage_v is None or not (0.0 <= voltage_v <= 36.0):
                continue

            pack_full_v = _CELL_FULL_V * _BATTERY_CELLS
            pack_empty_v = _CELL_EMPTY_V * _BATTERY_CELLS
            percent = None
            if pack_full_v > pack_empty_v:
                percent = round(
                    max(0.0, min(100.0, (voltage_v - pack_empty_v) /
                                 (pack_full_v - pack_empty_v) * 100.0)), 1
                )

            return {
                "available": True,
                "reason": None,
                "chip": chip,
                "address": hex(address),
                "voltage_v": round(voltage_v, 3),
                "current_ma": round(current_ma, 1) if current_ma is not None else None,
                "percent": percent,
                "cells": int(_BATTERY_CELLS),
                "timestamp": time.time(),
            }
        except Exception:
            continue

    return _unavailable("no battery monitor (INA219/INA260) found on the I2C bus")


# ── Barometer (BMP280 / BMP388) ───────────────────────────────────────────
def _s16(value):
    """Interpret an unsigned 16-bit register as a signed value."""
    return value - 0x10000 if value & 0x8000 else value


def _compensate_bmp280(calib, raw_temperature, raw_pressure):
    """Bosch BMP280 compensation (float reference used by Adafruit/Bosch).

    ``calib`` is (dig_T1..dig_T9); ``raw_temperature`` and ``raw_pressure`` are
    the 20-bit raw ADC values. Returns (temperature_c, pressure_hpa).
    """
    (dig_t1, dig_t2, dig_t3, dig_p1, dig_p2, dig_p3,
     dig_p4, dig_p5, dig_p6, dig_p7, dig_p8, dig_p9) = calib

    # --- Temperature ---
    var1 = (raw_temperature / 16384.0 - dig_t1 / 1024.0) * dig_t2
    var2 = ((raw_temperature / 131072.0 - dig_t1 / 8192.0) ** 2) * dig_t3
    t_fine = int(var1 + var2)
    temperature_c = t_fine / 5120.0

    # --- Pressure ---
    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * dig_p6 / 32768.0
    var2 += var1 * dig_p5 * 2.0
    var2 = var2 / 4.0 + dig_p4 * 65536.0
    var3 = dig_p3 * var1 * var1 / 524288.0
    var1 = (var3 + dig_p2 * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * dig_p1
    if not var1:
        raise ArithmeticError("invalid BMP280 calibration (dig_P1 is zero)")

    pressure = 1048576.0 - raw_pressure
    pressure = ((pressure - var2 / 4096.0) * 6250.0) / var1
    var1 = dig_p9 * pressure * pressure / 2147483648.0
    var2 = pressure * dig_p8 / 32768.0
    pressure += (var1 + var2 + dig_p7) / 16.0
    pressure_hpa = pressure / 100.0

    return temperature_c, pressure_hpa


def _read_bmp280(device):
    """Read a BMP280 and return (pressure_hpa, temperature_c)."""
    calib = (
        device.readU16(0x88),                                   # dig_T1 (unsigned)
        _s16(device.readU16(0x8A)),                             # dig_T2
        _s16(device.readU16(0x8C)),                             # dig_T3
        device.readU16(0x8E),                                   # dig_P1 (unsigned)
        _s16(device.readU16(0x90)),                             # dig_P2
        _s16(device.readU16(0x92)),                             # dig_P3
        _s16(device.readU16(0x94)),                             # dig_P4
        _s16(device.readU16(0x96)),                             # dig_P5
        _s16(device.readU16(0x98)),                             # dig_P6
        _s16(device.readU16(0x9A)),                             # dig_P7
        _s16(device.readU16(0x9C)),                             # dig_P8
        _s16(device.readU16(0x9E)),                             # dig_P9
    )
    # 0xF7..0xF9 = pressure (msb, lsb, xlsb), 0xFA..0xFC = temperature.
    raw_pressure = ((device.readU8(0xF7) << 12) | (device.readU8(0xF8) << 4) |
                    (device.readU8(0xF9) >> 4))
    raw_temperature = ((device.readU8(0xFA) << 12) | (device.readU8(0xFB) << 4) |
                       (device.readU8(0xFC) >> 4))

    return _compensate_bmp280(calib, raw_temperature, raw_pressure)


def read_barometer():
    """Read a real barometric pressure sensor for a barometric altitude."""
    if not _I2C_AVAILABLE:
        return _unavailable("no I2C support available")

    for address in (0x76, 0x77):
        try:
            bus = _get_i2c_bus()
            device = I2CDevice(bus, address)
            chip_id = device.readU8(0xD0)
            pressure_hpa = temperature_c = None

            if chip_id == 0x58:                     # BMP280
                pressure_hpa, temperature_c = _read_bmp280(device)
                chip = "BMP280"
            elif chip_id in (0x50, 0x51, 0x52):     # BMP388 / BMP390 / BMP391
                chip = {0x50: "BMP388", 0x51: "BMP390", 0x52: "BMP391"}[chip_id]
                raw = device.readU24(0x04)
                pressure_pa = raw / 256.0
                pressure_hpa = pressure_pa / 100.0
                temperature_c = _s16(device.readU16(0x07)) / 100.0
            else:
                # Unknown chip id - try the BMP280 layout anyway.
                pressure_hpa, temperature_c = _read_bmp280(device)
                chip = "BMP280-compatible"

            if pressure_hpa is None:
                continue

            altitude_m = 44330.0 * (1.0 - (pressure_hpa / _SEA_LEVEL_HPA) ** 0.1903)
            return {
                "available": True,
                "reason": None,
                "chip": chip,
                "address": hex(address),
                "pressure_hpa": round(pressure_hpa, 2),
                "temperature_c": round(temperature_c, 2) if temperature_c is not None else None,
                "altitude_m": round(altitude_m, 2),
                "sea_level_hpa": _SEA_LEVEL_HPA,
                "timestamp": time.time(),
            }
        except Exception:
            continue

    return _unavailable("no barometer (BMP280/BMP388) found on the I2C bus")


# ── Magnetometer (QMC5883L) ───────────────────────────────────────────────
def read_compass():
    """Read a real magnetometer for a magnetic heading."""
    if not _I2C_AVAILABLE:
        return _unavailable("no I2C support available")

    for address in (0x0D, 0x1E):
        try:
            bus = _get_i2c_bus()
            device = I2CDevice(bus, address)
            status = device.readU8(0x09)
            # The status register only ever reports the DRDY/OVL/DOR bits.
            if status & 0xF8:
                continue

            # Put the chip into continuous mode (soft reset first).
            try:
                device.writeU8(0x0A, 0x0B)      # soft reset
                time.sleep(0.01)
                device.writeU8(0x0A, 0x09)      # continuous measurement mode
                device.writeU8(0x0B, 0x00)      # +/- 2 G full scale
                device.writeU8(0x0C, 0x01)      # SET/RESET period
                device.writeU8(0x0D, 0x06)      # 50 Hz output data rate
                time.sleep(0.05)
            except Exception:
                pass

            x_l, x_h = device.readU8(0x00), device.readU8(0x01)
            y_l, y_h = device.readU8(0x02), device.readU8(0x03)
            x = int.from_bytes(bytes([x_l, x_h]), "little", signed=True)
            y = int.from_bytes(bytes([y_l, y_h]), "little", signed=True)
            if x == 0 and y == 0:
                continue
            heading = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
            return {
                "available": True,
                "reason": None,
                "chip": "QMC5883L",
                "address": hex(address),
                "heading_deg": round(heading, 1),
                "timestamp": time.time(),
            }
        except Exception:
            continue

    return _unavailable("no magnetometer (QMC5883L) found on the I2C bus")


def read_all():
    """Read every supported sensor and return their real status.

    The returned dictionary is safe to hand straight to the dashboard: it
    contains only measured values or explicit unavailability reasons.
    """
    return {
        "lora": probe_sx1278(),
        "gps": read_gps(),
        "battery": read_battery(),
        "barometer": read_barometer(),
        "compass": read_compass(),
    }
