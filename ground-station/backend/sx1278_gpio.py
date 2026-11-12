"""
sx1278_gpio.py - GPIO control for the SX1278 wiring on the AERIS ground station.

Actual wiring (user-verified 2026-11-12):

    MLX90640 (I2C)      VCC -> pin 1  (3.3V)   SDA -> pin 3  (GPIO 2)
                        GND -> pin 6  (GND)    SCL -> pin 5  (GPIO 3)

    Ra-02 SX1278        VCC -> pin 17 (3.3V)   MISO   -> pin 21 (GPIO 9)
    (SPI)               GND -> pin 9  (GND)    MOSI   -> pin 19 (GPIO 10)
                        SCK -> pin 23 (GPIO 11) NSS/CS -> pin 18 (GPIO 24)
                        RESET -> pin 16 (GPIO 23)     DIO0 -> pin 12 (GPIO 18)

The radio is NOT connected to the kernel SPI chip-select lines (CE0/GPIO8,
CE1/GPIO7). All access must therefore toggle GPIO24 manually:

  * SPI bus           : /dev/spidev0.0 (SCK GPIO11, MISO GPIO9, MOSI GPIO10)
  * manual chip select : GPIO24 (active-low, idle high)
  * reset             : GPIO23 (active-low; a pulse fully resets the radio)
  * IRQ (DIO0)        : GPIO18 (input)

pinctrl is used for the discrete GPIO edges - it writes the GPIO control
registers directly and is available to members of the `gpio` group.
"""

import subprocess
import time
import threading

NSS = 24
RESET = 23
DIO0 = 18

# Process-wide re-entrant lock: _sensor_poll_loop's re-probe and the LoRa RX
# watcher both drive GPIO24/GPIO10/GPIO11. Concurrent transactions interleave
# chip-select edges and corrupt bytes on BOTH sides - never allow that.
RADIO_LOCK = threading.RLock()


def _pinctrl(*args):
    subprocess.run(
        ["pinctrl", *[str(a) for a in args]],
        capture_output=True,
        check=False,
    )


def reset_pulse(low_seconds: float = 0.02, settle_seconds: float = 0.06):
    """Reset the radio: NRESET low, then high, then let it boot."""
    _pinctrl("set", RESET, "op", "dl")
    time.sleep(low_seconds)
    _pinctrl("set", RESET, "dh")
    time.sleep(settle_seconds)


def cs_low():
    """Select the radio (NSS active-low). Ensures the pin is an output."""
    _pinctrl("set", NSS, "op", "dl")


def cs_high():
    """Deselect the radio. Ensures the pin is an output idling high."""
    _pinctrl("set", NSS, "op", "dh")


def pin_level(pin: int) -> str:
    """Read the physical level of a pin: 'hi', 'lo' or 'unknown'."""
    out = subprocess.run(
        ["pinctrl", "get", str(pin)], capture_output=True, text=True, check=False
    ).stdout
    if "|" not in out:
        return "unknown"
    part = out.split("|")[1]
    if " hi" in part:
        return "hi"
    if " lo" in part:
        return "lo"
    return "unknown"
