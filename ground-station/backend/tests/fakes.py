"""Hardware and client stand-ins used by the AERIS tests.

Every fake here replaces a REAL device, so the test suite never opens
``/dev/i2c-*``, never opens ``/dev/spidev*``, never toggles a real GPIO
line and never transmits a LoRa packet.
"""
import asyncio
import time


# ── SX1278 / SX1276 radio ───────────────────────────────────────────────────
class FakeSpi:
    """In-memory SX127x register file.

    Records every write and answers reads from a register dictionary, so the
    tests can assert exactly which register was programmed with which value.
    """

    def __init__(self, registers=None, read_overrides=None):
        self.registers = dict(registers or {})
        # Values a read answers with, regardless of what was written. Used
        # to prove that the code READS a register back instead of assuming
        # its content.
        self.read_overrides = dict(read_overrides or {})
        self.writes = []          # [(addr, value)]
        self.reads = []           # [addr]
        self.closed = False
        # spidev attributes the real code assigns
        self.max_speed_hz = 0
        self.mode = 0
        self.lsbfirst = False
        self.no_cs = True

    # -- spidev-like API --------------------------------------------------
    def read(self, addr):
        self.reads.append(addr)
        if addr in self.read_overrides:
            return self.read_overrides[addr]
        return self.registers.get(addr, 0)

    def write(self, addr, value):
        self.writes.append((addr, value))
        self.registers[addr] = value

    def close(self):
        self.closed = True

    def written_to(self, addr):
        """All values written to one register."""
        return [value for a, value in self.writes if a == addr]


class FakeSensors:
    """Stand-in for the ``sensors`` module as seen by lora_rx_service.

    ``lora_rx_service`` reaches the radio through
    ``sensors.sx1278_init()`` / ``sensors._sx1278_read_reg()`` /
    ``sensors._sx1278_write_reg()``. Replacing the whole module means the
    tests never touch a real SPI device and never toggle GPIO24.
    """

    def __init__(self, registers=None, version=0x12, available=True,
                 read_overrides=None):
        self.registers = dict(registers or {})
        self.read_overrides = dict(read_overrides or {})
        self.version = version
        self.available = available
        self.init_calls = 0
        self.handles = []          # every FakeSpi that was handed out

    def sx1278_init(self, spi_bus=0, frequency_hz=433_000_000):
        self.init_calls += 1
        spi = FakeSpi(self.registers, read_overrides=self.read_overrides)
        self.handles.append(spi)
        if not self.available:
            return spi, {"available": False, "reason": "fake radio not present"}
        return spi, {
            "available": True,
            "reason": None,
            "model": "SX1276/SX1278",
            "version_register": hex(self.version),
            "frequency_mhz": 433.0,
        }

    def _sx1278_read_reg(self, spi, addr):
        return spi.read(addr)

    def _sx1278_write_reg(self, spi, addr, value):
        spi.write(addr, value)

    def sx1278_read_fifo(self, spi, count):
        return b""


# ── MLX90640 thermal camera ────────────────────────────────────────────────
class FakeI2C:
    """Stand-in for the ``busio.I2C`` handle (only close() matters here)."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeMLX90640:
    """Stand-in for ``adafruit_mlx90640.MLX90640``.

    Modes:
      ``ok``     - delivers a valid frame every ``frame_interval`` seconds
      ``block``  - mimics the vendor's unbounded "while dataReady == 0"
                   wait: it polls the status register through the guarded
                   ``_I2CReadWords`` until the data-ready bit is set
      ``raise``  - every read raises an I2C error
      ``nan``    - delivers a frame whose first ``nan_count`` samples are
                   not finite
    """

    def __init__(self, mode="ok", nan_count=0, frame_interval=0.2,
                 values=None):
        self.mode = mode
        self.nan_count = nan_count
        self.frame_interval = frame_interval
        self.values = list(values) if values else [25.0] * 768
        self.calls = 0
        self.reads = 0
        self.refresh_rate = 3
        self.i2c_device = None
        self._aeris_guard_armed = False

    # -- the two primitives the deadline guard wraps ----------------------
    def _I2CReadWords(self, addr, buffer, *, end=None):
        self.reads += 1
        if self.mode == "raise":
            raise RuntimeError("I2C read failed")
        for i in range(len(buffer)):
            buffer[i] = 0
        return None

    def _I2CWriteWord(self, writeAddress, data):
        return None

    # -- the public API the capture loop uses ------------------------------
    def getFrame(self, framebuf):
        self.calls += 1
        if self.mode == "block":
            # Mimic adafruit_mlx90640._GetFrameData: wait for the data-ready
            # bit. The real driver has NO timeout here - the guard installed
            # by _arm_frame_guard() is what makes this wait bounded.
            status = [0]
            while True:
                self._I2CReadWords(0x8000, status)
                if status[0] & 0x0008:
                    break
            return None
        if self.mode == "raise":
            raise RuntimeError("I2C read failed")
        time.sleep(self.frame_interval)
        for i in range(768):
            framebuf[i] = self.values[i]
        if self.mode == "nan":
            for i in range(min(self.nan_count, 768)):
                framebuf[i] = float("nan")
        return None


# ── WebSocket clients ───────────────────────────────────────────────────────
class FakeWebSocket:
    """Stand-in for a Starlette WebSocket connection."""

    def __init__(self, name="client", fail=False, stall=False,
                 disconnect=False):
        self.name = name
        self.fail = fail
        self.stall = stall
        self.disconnect = disconnect
        self.received = []
        self.closed = False
        self.close_code = None
        self.application_state = None

    async def accept(self):
        self.application_state = "connected"

    async def send_json(self, message):
        if self.stall:
            # A client that stopped reading: the send never completes.
            await asyncio.sleep(3600)
            return
        if self.fail:
            raise RuntimeError("fake client is broken")
        self.received.append(message)

    async def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code

    async def receive_text(self):
        if self.disconnect:
            raise ConnectionError("client went away")
        await asyncio.sleep(3600)
        return ""
