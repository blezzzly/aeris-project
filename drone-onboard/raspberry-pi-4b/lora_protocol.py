"""
lora_protocol.py - AERIS LoRa Packet Framing Protocol

Provides compact binary framing for drone <-> ground LoRa communication:
  - Magic + version + type + flags + seq + ack + length + CRC16
  - Packet types: THERMAL_DATA, TELEMETRY, ACK, CMD, CMD_ACK
  - Encode/decode helpers, CRC16 validation, dedup/reassembly support
"""
import struct
import zlib

MAGIC = b"\xAE\x52"
PROTOCOL_VERSION = 1

# Packet types
PKT_THERMAL_DATA = 0x01
PKT_TELEMETRY = 0x02
PKT_ACK = 0x03
PKT_CMD = 0x04
PKT_CMD_ACK = 0x05

# Flags
FLAG_REQUIRES_ACK = 0x01
FLAG_IS_ACK = 0x02
FLAG_RETRANSMISSION = 0x04

# Header: magic(2) + version(1) + type(1) + flags(1) + seq(2) + ack_seq(2) + length(2) = 11
HEADER_FMT = "!2sBBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 11
CRC_SIZE = 2


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_packet(pkt_type: int, payload: bytes, seq: int = 0, ack_seq: int = 0,
                  flags: int = 0, requires_ack: bool = False) -> bytes:
    """Encode a complete framed packet with CRC."""
    if requires_ack:
        flags |= FLAG_REQUIRES_ACK
    length = len(payload)
    if length > 240:
        raise ValueError(f"Payload too large: {length} > 240 bytes")
    header = struct.pack(HEADER_FMT, MAGIC, PROTOCOL_VERSION, pkt_type, flags,
                         seq & 0xFFFF, ack_seq & 0xFFFF, length)
    crc_input = header + payload
    crc = crc16(crc_input)
    return header + payload + struct.pack("!H", crc)


def decode_packet(data: bytes):
    """Decode and validate a framed packet. Returns dict or None if invalid."""
    if len(data) < HEADER_SIZE + CRC_SIZE:
        return None
    magic, version, pkt_type, flags, seq, ack_seq, length = struct.unpack(
        HEADER_FMT, data[:HEADER_SIZE]
    )
    if magic != MAGIC:
        return None
    if version != PROTOCOL_VERSION:
        return None
    if len(data) != HEADER_SIZE + length + CRC_SIZE:
        return None
    payload = data[HEADER_SIZE:HEADER_SIZE + length]
    crc_received = struct.unpack("!H", data[HEADER_SIZE + length:HEADER_SIZE + length + CRC_SIZE])[0]
    crc_calc = crc16(data[:HEADER_SIZE + length])
    if crc_received != crc_calc:
        return None
    return {
        "version": version,
        "type": pkt_type,
        "flags": flags,
        "seq": seq,
        "ack_seq": ack_seq,
        "length": length,
        "payload": payload,
        "requires_ack": bool(flags & FLAG_REQUIRES_ACK),
    }


def encode_thermal_payload(frame_id: int, chunk_idx: int, total_chunks: int,
                           temp_min: float, temp_max: float,
                           hotspot_x: int, hotspot_y: int,
                           detected: bool, confidence: float,
                           timestamp: str, chunk_bytes: bytes,
                           gps_lat=None, gps_lng=None, battery=None) -> bytes:
    """Encode thermal data chunk payload (compact binary)."""
    import json
    meta = {
        "f": frame_id,
        "c": chunk_idx,
        "t": total_chunks,
        "mn": round(temp_min, 1),
        "mx": round(temp_max, 1),
        "hx": hotspot_x,
        "hy": hotspot_y,
        "det": detected,
        "conf": round(confidence, 2),
        "ts": timestamp,
    }
    if gps_lat is not None:
        meta["lat"] = gps_lat
    if gps_lng is not None:
        meta["lng"] = gps_lng
    if battery is not None:
        meta["bat"] = battery
    meta_bytes = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    # meta length (1 byte) + meta + chunk
    if len(meta_bytes) > 255:
        raise ValueError("Metadata too large")
    return struct.pack("!B", len(meta_bytes)) + meta_bytes + chunk_bytes


def decode_thermal_payload(payload: bytes):
    """Decode thermal data chunk payload. Returns (meta_dict, chunk_bytes) or None."""
    import json
    if len(payload) < 1:
        return None
    meta_len = payload[0]
    if len(payload) < 1 + meta_len:
        return None
    meta_bytes = payload[1:1 + meta_len]
    chunk_bytes = payload[1 + meta_len:]
    try:
        meta = json.loads(meta_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return meta, chunk_bytes


# Reassembly helper
class FrameReassembler:
    """Reassemble multi-chunk thermal frames by frame_id."""

    def __init__(self, max_frames: int = 16):
        self._frames = {}  # frame_id -> {chunks: {idx: bytes}, meta, total, received}
        self._max = max_frames

    def add_chunk(self, meta: dict, chunk_bytes: bytes):
        """Add a chunk. Returns (frame_id, complete, full_frame_bytes, metadata) or None."""
        frame_id = meta.get("f")
        chunk_idx = meta.get("c", 0)
        total = meta.get("t", 1)
        if frame_id is None:
            return None
        if frame_id not in self._frames:
            if len(self._frames) >= self._max:
                # evict oldest
                oldest = min(self._frames.keys())
                del self._frames[oldest]
            self._frames[frame_id] = {
                "chunks": {},
                "meta": meta,
                "total": total,
                "received": 0,
            }
        f = self._frames[frame_id]
        if chunk_idx in f["chunks"]:
            return None  # duplicate chunk
        f["chunks"][chunk_idx] = chunk_bytes
        f["received"] += 1
        f["meta"] = meta  # update meta (last chunk's meta)
        if f["received"] >= f["total"]:
            # reassemble
            full = bytearray()
            for i in range(f["total"]):
                if i not in f["chunks"]:
                    return None  # missing chunk
                full.extend(f["chunks"][i])
            del self._frames[frame_id]
            return (frame_id, True, bytes(full), f["meta"])
        return (frame_id, False, None, None)

    def pending_count(self) -> int:
        return len(self._frames)