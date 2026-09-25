"""CRC-16/Modbus (полином 0xA001)."""
import struct


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc


def append_crc(body: bytes) -> bytes:
    """Добавить CRC-16/Modbus (little-endian) к телу кадра."""
    return body + struct.pack('<H', crc16(body))
