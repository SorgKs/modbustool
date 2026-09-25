"""Формат лога кадров: запись и разбор только здесь (править синхронно).

logformat 1:
  HH:MM:SS.mmm  +DTms  REASON  [hex…]
  REASON ∈ OK | TX | TIMEOUT | CRC-BAD | SHORT/NOISE
  Без rx/calc/len/inf/GAP — восстанавливаются при разборе.
"""
import re
import struct
import time

from crc import crc16


LOG_FORMAT_VERSION = 1

REASONS = ('OK', 'TX', 'TIMEOUT', 'CRC-BAD', 'SHORT/NOISE')
_REASONS_ALT = '|'.join(re.escape(r) for r in REASONS)

# группы: 1=time 2=dt_ms 3=reason 4=hex?
_LINE_RE = re.compile(
    rf'^(\d{{2}}:\d{{2}}:\d{{2}}\.\d{{3}})\s+\+\s*([-\d.]+)ms\s+'
    rf'({_REASONS_ALT})'
    rf'(?:\s+((?:[0-9a-fA-F]{{2}})(?:\s+[0-9a-fA-F]{{2}})*))?\s*$'
)


def format_marker() -> str:
    return f'--- logformat {LOG_FORMAT_VERSION} ---'


def stamp(t: float) -> str:
    return time.strftime('%H:%M:%S', time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


def normalize_reason(reason: str) -> str:
    """UI-reason → стабильный токен лога."""
    if reason.startswith('CRC-BAD'):
        return 'CRC-BAD'
    return reason


def format_line(t_end: float, prev_end: float, reason: str, raw: bytes) -> str:
    """Одна строка лога format 1."""
    dt = (t_end - prev_end) * 1000 if prev_end > 0 else 0.0
    r = normalize_reason(reason)
    head = f"{stamp(t_end)}  +{dt:7.1f}ms  {r}"
    if r == 'TIMEOUT' or not raw:
        return head
    return f"{head}  {raw.hex(' ')}"


def _crc_ok(raw: bytes, reason: str) -> bool | None:
    if reason == 'TIMEOUT' or len(raw) < 4:
        return None
    body = raw[:-2]
    rx = struct.unpack('<H', raw[-2:])[0]
    ok = crc16(body) == rx
    if reason == 'CRC-BAD':
        return False
    if reason in ('OK', 'TX') and not ok:
        return ok
    return ok


def parse_line(line: str, last_t: float) -> tuple[dict | None, float]:
    """Разбор одной строки. Возвращает (event|None, новый last_t)."""
    line = line.strip()
    if not line or line.startswith('---'):
        return None, last_t
    m = _LINE_RE.match(line)
    if not m:
        return None, last_t

    dt_ms = float(m.group(2))
    reason = m.group(3)
    if last_t == 0.0 and dt_ms == 0.0:
        t = 1.0
    else:
        t = last_t + max(dt_ms, 0.0) / 1000.0

    if reason == 'TIMEOUT':
        return {'t': t, 'reason': 'TIMEOUT', 'raw': b'', 'crc_ok': None}, t

    raw = b''
    hexpart = m.group(4)
    if hexpart:
        try:
            raw = bytes(int(x, 16) for x in hexpart.split())
        except ValueError:
            raw = b''

    return {
        't': t,
        'reason': reason,
        'raw': raw,
        'crc_ok': _crc_ok(raw, reason),
    }, t


def parse_text(text: str) -> list[dict]:
    """Весь текст лога → список events для analyze_events."""
    evs = []
    last_t = 0.0
    for line in text.splitlines():
        ev, last_t = parse_line(line, last_t)
        if ev is not None:
            evs.append(ev)
    return evs
