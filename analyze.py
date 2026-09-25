"""Анализ логов/history: таблица запросов мастера со статистикой."""
import math
import struct
from collections import defaultdict

from crc import crc16
from logformat import parse_text
from protocol import parse_pdu, req_key


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(var)


def _status(row: dict) -> str:
    req_ok = row['req_ok']
    resp_ok = row['resp_ok']
    resp_bad = row['resp_bad']
    if resp_ok == 0:
        return 'silent'
    if resp_bad > 0 and resp_bad >= resp_ok:
        return 'error'
    rate = resp_ok / req_ok if req_ok else 0.0
    jitter = row.get('jitter_pct')
    if rate >= 0.95:
        if jitter is not None and jitter > 25.0:
            return 'irregular'
        return 'ok'
    if rate >= 0.50:
        return 'flaky'
    return 'silent'


def _new_row(key: tuple) -> dict:
    slave, typ, direction, addr, qty = key
    return {
        'key': key,
        'slave': slave, 'type': typ, 'direction': direction,
        'addr': addr, 'qty': qty,
        'req_ok': 0, 'req_bad': 0,
        'resp_ok': 0, 'resp_bad': 0, 'resp_miss': 0,
        'ok_times': [],
        'period_ms': None, 'jitter_pct': None, 'status': 'silent',
    }


def _finalize(rows: dict) -> list[dict]:
    out = []
    for row in rows.values():
        times = row['ok_times']
        if len(times) >= 2:
            intervals = [(times[i] - times[i - 1]) * 1000.0
                         for i in range(1, len(times))]
            med = _median(intervals)
            sd = _std(intervals)
            row['period_ms'] = med
            row['jitter_pct'] = (100.0 * sd / med) if med and sd is not None and med > 0 else 0.0
        else:
            row['period_ms'] = None
            row['jitter_pct'] = None
        row['status'] = _status(row)
        out.append(row)
    out.sort(key=lambda r: (r['slave'], r['type'], r['direction'], r['addr'], r['qty']))
    return out


class _Pending:
    __slots__ = ('key', 'fc', 'slave', 't')

    def __init__(self, key, fc, slave, t):
        self.key = key
        self.fc = fc
        self.slave = slave
        self.t = t


def analyze_events(events: list[dict]) -> list[dict]:
    """events: {t, reason, raw, crc_ok} — упорядочены по времени.

    raw: bytes | None; reason: OK|TX|TIMEOUT|CRC-BAD|...
    """
    rows: dict[tuple, dict] = {}
    pending: _Pending | None = None

    def ensure(key):
        if key not in rows:
            rows[key] = _new_row(key)
        return rows[key]

    def close_miss():
        nonlocal pending
        if pending is not None:
            ensure(pending.key)['resp_miss'] += 1
            pending = None

    for ev in events:
        reason = ev.get('reason') or ''
        raw = ev.get('raw') or b''
        t = float(ev.get('t') or 0.0)
        crc_ok = ev.get('crc_ok')

        if reason == 'TIMEOUT' or (not raw and 'TIMEOUT' in reason):
            if pending is not None:
                ensure(pending.key)['resp_miss'] += 1
                pending = None
            continue

        if len(raw) < 2:
            continue

        body = raw[:-2] if len(raw) >= 4 else raw
        if crc_ok is None and len(raw) >= 4:
            rx = struct.unpack('<H', raw[-2:])[0]
            crc_ok = crc16(body) == rx

        info = parse_pdu(body)
        if info is None:
            continue

        if info['kind'] == 'req':
            key = req_key(info)
            if key is None:
                continue
            # FC05/06: ответ — echo, парсер снова даёт kind=req
            if (pending and pending.key == key and info['fc'] in (0x05, 0x06)):
                row = ensure(key)
                if crc_ok:
                    row['resp_ok'] += 1
                    row['ok_times'].append(t)
                else:
                    row['resp_bad'] += 1
                pending = None
                continue
            close_miss()
            row = ensure(key)
            if crc_ok:
                row['req_ok'] += 1
                pending = _Pending(key, info['fc'], info['slave'], t)
            else:
                row['req_bad'] += 1
                pending = None
            continue

        if info['kind'] == 'exc':
            if pending and pending.slave == info['slave'] and pending.fc == info['fc']:
                ensure(pending.key)['resp_bad'] += 1
                pending = None
            continue

        if info['kind'] == 'resp':
            if pending and pending.slave == info['slave'] and pending.fc == info['fc']:
                row = ensure(pending.key)
                if crc_ok:
                    row['resp_ok'] += 1
                    row['ok_times'].append(t)
                else:
                    row['resp_bad'] += 1
                pending = None
            continue

    close_miss()
    return _finalize(rows)


def events_from_frames(frames) -> list[dict]:
    """Список Frame → events для analyze_events."""
    evs = []
    for fr in frames:
        raw = fr.raw or b''
        crc_ok = None
        if fr.reason == 'TIMEOUT':
            crc_ok = None
        elif len(raw) >= 4:
            body = raw[:-2]
            rx = struct.unpack('<H', raw[-2:])[0]
            crc_ok = crc16(body) == rx
        evs.append({
            't': fr.t_end,
            'reason': fr.reason,
            'raw': raw,
            'crc_ok': crc_ok,
        })
    return evs


def events_from_log_text(text: str) -> list[dict]:
    """Разбор лога — делегирует в logformat."""
    return parse_text(text)


def analyze_frames(frames) -> list[dict]:
    return analyze_events(events_from_frames(frames))


def analyze_log_file(path: str) -> list[dict]:
    with open(path, encoding='utf-8', errors='replace') as f:
        return analyze_events(events_from_log_text(f.read()))
