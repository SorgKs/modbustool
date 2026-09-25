"""Сценарий mbgen: шаг таблицы → кадры, drop/corrupt."""
import copy
import random

from master import build_request, resolve_fc
from protocol import build_response


ROLES = ('req', 'resp', 'pair')
TYPES = ('holding', 'input', 'coils')
DIRS = ('read', 'write')


def default_step() -> dict:
    """Строка по умолчанию: req holding read addr=1 qty=1."""
    return {
        'role': 'req',
        'slave': 1,
        'type': 'holding',
        'direction': 'read',
        'addr': 1,
        'qty': 1,
        'values': [],
        'delay_ms_min': 0,
        'delay_ms_max': 0,
        'resp_delay_ms_min': 0,
        'resp_delay_ms_max': 0,
    }


def step_label(step: dict) -> str:
    vals = step.get('values') or []
    extra = f" vals={vals}" if vals else ''
    return (f"{step['role']} id={step['slave']} {step['type']} "
            f"{step['direction']} addr={step['addr']} qty={step['qty']}{extra}")


def _poll_from_step(step: dict) -> dict:
    return {
        'slave': int(step['slave']),
        'type': step['type'],
        'direction': step['direction'],
        'addr': int(step['addr']),
        'qty': int(step['qty']),
        'values': list(step.get('values') or []),
    }


def _req_info(step: dict) -> dict:
    """Словарь как у parse_pdu kind=req для build_response."""
    poll = _poll_from_step(step)
    return {
        'kind': 'req',
        'slave': poll['slave'],
        'fc': resolve_fc(poll),
        'type': poll['type'],
        'direction': poll['direction'],
        'addr': poll['addr'],
        'qty': poll['qty'],
    }


def validate_step(step: dict) -> None:
    role = step.get('role')
    if role not in ROLES:
        raise ValueError(f'role must be {ROLES}')
    if step.get('type') not in TYPES:
        raise ValueError(f'type must be {TYPES}')
    if step.get('direction') not in DIRS:
        raise ValueError(f'direction must be {DIRS}')
    poll = _poll_from_step(step)
    resolve_fc(poll)  # input+write и пр.
    qty = poll['qty']
    if qty < 1:
        raise ValueError('qty must be >= 1')
    vals = poll['values']
    needs_vals = (
        poll['direction'] == 'write'
        or role in ('resp', 'pair')
    )
    if needs_vals and role == 'req' and poll['direction'] == 'write':
        if len(vals) != qty:
            raise ValueError(f'write req needs {qty} values')
    if role in ('resp', 'pair'):
        # для ответа: values длины qty (или пусто → нули в build_response)
        if vals and len(vals) != qty:
            raise ValueError(f'resp needs {qty} values or empty')
    if role == 'pair' and poll['direction'] == 'write' and len(vals) != qty:
        raise ValueError(f'pair write needs {qty} values')
    dmin = int(step.get('delay_ms_min', 0))
    dmax = int(step.get('delay_ms_max', 0))
    rmin = int(step.get('resp_delay_ms_min', 0))
    rmax = int(step.get('resp_delay_ms_max', 0))
    if dmin < 0 or dmax < 0 or rmin < 0 or rmax < 0:
        raise ValueError('step delay >= 0')
    if dmin > dmax or rmin > rmax:
        raise ValueError('step delay min > max')


def frames_for_step(step: dict) -> list[bytes]:
    """1 кадр (req|resp) или 2 (pair)."""
    validate_step(step)
    role = step['role']
    poll = _poll_from_step(step)
    vals = list(step.get('values') or []) or None
    if role == 'req':
        return [build_request(poll)]
    if role == 'resp':
        return [build_response(_req_info(step), vals)]
    # pair
    return [build_request(poll), build_response(_req_info(step), vals)]


def _corrupt_body(frame: bytes) -> bytes:
    """Подмена 1 байта в теле PDU (после slave+FC, до CRC); CRC не трогаем."""
    if len(frame) < 5:
        return frame
    # индексы данных: [2 .. len-3]
    lo, hi = 2, len(frame) - 3
    if lo > hi:
        return frame
    idx = random.randint(lo, hi)
    buf = bytearray(frame)
    new_b = buf[idx] ^ 0xFF
    if new_b == buf[idx]:
        new_b = (buf[idx] + 1) & 0xFF
    buf[idx] = new_b
    return bytes(buf)


def maybe_emit(frame: bytes, drop_pct: float, corrupt_pct: float):
    """(None|bytes, tag): DROP / OK / CORRUPT. CRC при corrupt не пересчитывается."""
    drop_pct = max(0.0, min(100.0, float(drop_pct)))
    corrupt_pct = max(0.0, min(100.0, float(corrupt_pct)))
    if drop_pct > 0 and random.random() * 100.0 < drop_pct:
        return None, 'DROP'
    out = frame
    tag = 'OK'
    if corrupt_pct > 0 and random.random() * 100.0 < corrupt_pct:
        out = _corrupt_body(frame)
        tag = 'CORRUPT'
    return out, tag


def clone_step(step: dict) -> dict:
    return copy.deepcopy(step)
