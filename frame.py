"""Разобранный кадр: классификация, заголовок, raw/inf-строки, лог-строки."""
import struct
import time

from crc import crc16
from protocol import decode_body, format_exception


def stamp(t: float) -> str:
    return time.strftime('%H:%M:%S', time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


class Frame:
    """Один принятый кадр и его представления для экрана/лога."""

    def __init__(self, raw: bytes, t_end: float, prev_end: float, reason: str):
        self.raw = raw
        self.t_end = t_end
        self.prev_end = prev_end
        self.reason = reason

    @property
    def is_noise(self) -> bool:
        return len(self.raw) < 4

    # --- рендер ---
    def header(self) -> str:
        dt = (self.t_end - self.prev_end) * 1000 if self.prev_end else 0.0
        head = f"{stamp(self.t_end)}  +{dt:7.1f}ms  {self.reason}"
        if self.is_noise:
            head += f"  len={len(self.raw)}"
        else:
            body = self.raw[:-2]
            rx = struct.unpack('<H', self.raw[-2:])[0]
            calc = crc16(body)
            head += f"  rx={rx:04X} calc={calc:04X}  len={len(self.raw)}"
        return head

    def raw_line(self):
        if not self.raw:
            return None
        return f"    raw : {self.raw.hex(' ')}"

    def inf_line(self, exc_mode: str = 'both'):
        if self.is_noise:
            return None
        body = self.raw[:-2]
        try:
            d = decode_body(body)
        except Exception as e:
            return f"    inf : (decode error: {e})"
        if isinstance(d, tuple) and d and d[0] == 'EXC':
            _, slave, base, code = d
            return f"    inf : {format_exception(slave, base, code, exc_mode)}"
        return f"    inf : {d}"

    def log_lines(self):
        """Полный набор строк для файла — всегда raw + inf(both)."""
        lines = [self.header()]
        r = self.raw_line()
        if r:
            lines.append(r)
        i = self.inf_line('both')
        if i:
            lines.append(i)
        return lines


def classify(raw: bytes, t_end: float, prev_end: float, bad_counter: int):
    """Классифицирует кадр.

    Возвращает (frame_or_None, is_good, new_bad_counter).
    frame_or_None = None, если CRC сошёлся (на экран не выводим).
    """
    if len(raw) < 4:
        return Frame(raw, t_end, prev_end, "SHORT/NOISE"), False, bad_counter
    body = raw[:-2]
    rx = struct.unpack('<H', raw[-2:])[0]
    if crc16(body) == rx:
        return None, True, bad_counter
    bad_counter += 1
    return Frame(raw, t_end, prev_end, f"CRC-BAD #{bad_counter}"), False, bad_counter
