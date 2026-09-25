"""Сборка кадров Modbus RTU: пауза > gap и/или валидный CRC."""
import struct

from crc import crc16


class FrameAssembler:
    """Копит байты; пауза > gap (1.5 символа) или готовый CRC — конец кадра.

    CRC-peel нужен, когда несколько кадров пришли одним chunk (TCP/склейка).
    Если граница по CRC при тишине ≤ gap — флаг tight_gap.
    """

    def __init__(self, gap_seconds: float):
        self.gap = gap_seconds
        self._buf = bytearray()
        self._last_rx = 0.0
        self._prev_end = 0.0

    def feed(self, chunk: bytes, now: float):
        """Возвращает список (frame_bytes, t_end, prev_end, tight_gap)."""
        out = []
        if chunk:
            if self._buf and (now - self._last_rx) > self.gap:
                out.extend(self._emit_gap_flush())
            self._buf.extend(chunk)
            self._last_rx = now
            out.extend(self._peel_crc(now, by_crc=True))
        else:
            if self._buf and (now - self._last_rx) > self.gap:
                out.extend(self._emit_gap_flush())
        return out

    def _emit_one(self, frame: bytes, t_end: float, tight_gap: bool):
        prev = self._prev_end
        self._prev_end = t_end
        return frame, t_end, prev, tight_gap

    def _peel_crc(self, t_end: float, by_crc: bool):
        """Вырезать кратчайшие кадры с верным CRC."""
        out = []
        while len(self._buf) >= 4:
            found = None
            for end in range(4, len(self._buf) + 1):
                body = self._buf[: end - 2]
                got = struct.unpack_from('<H', self._buf, end - 2)[0]
                if crc16(body) == got:
                    found = end
                    break
            if found is None:
                break
            frame = bytes(self._buf[:found])
            del self._buf[:found]
            # граница по CRC и пауза с предыдущего кадра ≤ 1.5 символа
            tight = bool(
                by_crc
                and self._prev_end > 0
                and (t_end - self._prev_end) <= self.gap
            )
            out.append(self._emit_one(frame, t_end, tight))
        return out

    def _emit_gap_flush(self):
        """Пауза истекла: CRC-кадры без tight, остаток — как есть."""
        out = self._peel_crc(self._last_rx, by_crc=False)
        if self._buf:
            frame = bytes(self._buf)
            self._buf.clear()
            out.append(self._emit_one(frame, self._last_rx, False))
        return out
