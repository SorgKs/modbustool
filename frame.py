"""Разобранный кадр: классификация, одна строка на пакет, лог."""
import struct
import time

from crc import crc16
from protocol import decode_body, format_exception
from logformat import format_line


# лимиты сокращения на экране
RAW_BYTES_MAX = 16
INF_CHARS_MAX = 72


def stamp(t: float) -> str:
    return time.strftime('%H:%M:%S', time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


def _clip(s: str, n: int) -> str:
    if n is None or len(s) <= n:
        return s
    return s[: max(n - 1, 0)] + '…'


class Frame:
    """Один принятый кадр и его представления для экрана/лога."""

    def __init__(self, raw: bytes, t_end: float, prev_end: float, reason: str,
                 tight_gap: bool = False):
        self.raw = raw
        self.t_end = t_end
        self.prev_end = prev_end
        self.reason = reason
        self.tight_gap = tight_gap

    @property
    def is_noise(self) -> bool:
        return len(self.raw) < 4

    def header(self) -> str:
        dt = (self.t_end - self.prev_end) * 1000 if self.prev_end > 0 else 0.0
        head = f"{stamp(self.t_end)}  +{dt:7.1f}ms  {self.reason}"
        if self.reason == 'TIMEOUT' or not self.raw:
            return head
        if self.is_noise:
            head += f"  len={len(self.raw)}"
        else:
            body = self.raw[:-2]
            rx = struct.unpack('<H', self.raw[-2:])[0]
            calc = crc16(body)
            head += f"  rx={rx:04X} calc={calc:04X}  len={len(self.raw)}"
        return head

    def raw_text(self, base: str = 'hex', max_bytes: int | None = RAW_BYTES_MAX):
        """Байты кадра; при max_bytes — сокращение."""
        if not self.raw:
            return None
        data = self.raw
        more = False
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
            more = True
        if base == 'dec':
            nums = ' '.join(f'{b:3d}' for b in data)
        else:
            nums = data.hex(' ')
        if more:
            nums += ' …'
        return nums

    def inf_text(self, exc_mode: str = 'both', max_chars: int | None = INF_CHARS_MAX):
        """Разбор тела; при max_chars — сокращение."""
        if self.is_noise:
            return None
        body = self.raw[:-2]
        try:
            d = decode_body(body)
        except Exception as e:
            s = f"(decode error: {e})"
            return _clip(s, max_chars)
        if isinstance(d, tuple) and d and d[0] == 'EXC':
            _, slave, base, code = d
            s = format_exception(slave, base, code, exc_mode)
        else:
            s = str(d)
        return _clip(s, max_chars)

    def parts(self, show_raw: bool, show_inf: bool, base: str, exc_mode: str,
              compact: bool = True):
        """Фрагменты одной строки: [(text, tag), ...]. compact — сокращать."""
        if self.reason == 'TX':
            tag = 'tx'
        elif self.reason == 'TIMEOUT':
            tag = 'timeout'
        elif self.is_noise:
            tag = 'noise'
        elif self.tight_gap:
            tag = 'gap'
        elif self.reason == 'OK':
            tag = 'ok'
        else:
            tag = 'head'

        mb = RAW_BYTES_MAX if compact else None
        mc = INF_CHARS_MAX if compact else None
        out = [(self.header(), tag)]
        if show_raw and self.raw:
            r = self.raw_text(base, mb)
            if r:
                out.append((f'  |  {r}', 'raw'))
        if show_inf and self.raw and self.reason != 'TIMEOUT':
            i = self.inf_text(exc_mode, mc)
            if i:
                out.append((f'  |  {i}', 'inf'))
        if self.tight_gap:
            out.append(('  GAP<1.5', 'gap'))
        return out

    def log_lines(self):
        """Строка файла через logformat (синхронно с парсером)."""
        return [format_line(self.t_end, self.prev_end, self.reason, self.raw or b'')]


def classify(raw: bytes, t_end: float, prev_end: float, bad_counter: int,
             tight_gap: bool = False):
    """Классифицирует кадр.

    Возвращает (frame, is_good, new_bad_counter).
    """
    if len(raw) < 4:
        return Frame(raw, t_end, prev_end, "SHORT/NOISE", tight_gap), False, bad_counter
    body = raw[:-2]
    rx = struct.unpack('<H', raw[-2:])[0]
    if crc16(body) == rx:
        return Frame(raw, t_end, prev_end, "OK", tight_gap), True, bad_counter
    bad_counter += 1
    return Frame(raw, t_end, prev_end, f"CRC-BAD #{bad_counter}", tight_gap), False, bad_counter
