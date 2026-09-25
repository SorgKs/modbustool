"""Разбор тела кадра Modbus RTU (без CRC)."""
import struct

from crc import append_crc


EXCEPTIONS = {
    1: "illegal function", 2: "illegal data address", 3: "illegal data value",
    4: "slave device failure", 5: "acknowledge", 6: "slave device busy",
    7: "negative acknowledge", 8: "memory parity error",
    10: "gateway path unavailable", 11: "gateway target device failed",
}

READ_FC = {1: "coils", 2: "discrete", 3: "holding", 4: "input"}


def parse_pdu(body: bytes) -> dict | None:
    """Структурированный разбор PDU без CRC.

    kind: req|resp|exc
    поля: slave, fc, type, direction, addr, qty [, code]
    """
    if len(body) < 2:
        return None
    slave, fc = body[0], body[1]

    if fc & 0x80:
        base = fc & 0x7F
        code = body[2] if len(body) > 2 else -1
        typ = READ_FC.get(base)
        if base in (0x05, 0x0F):
            typ = 'coils'
        elif base in (0x06, 0x10):
            typ = 'holding'
        return {
            'kind': 'exc', 'slave': slave, 'fc': base, 'type': typ,
            'direction': 'read' if base in READ_FC else 'write',
            'addr': None, 'qty': None, 'code': code,
        }

    if fc in READ_FC:
        name = READ_FC[fc]
        if len(body) == 6:
            addr, qty = struct.unpack('>HH', body[2:6])
            return {
                'kind': 'req', 'slave': slave, 'fc': fc, 'type': name,
                'direction': 'read', 'addr': addr, 'qty': qty,
            }
        if len(body) >= 3:
            bc = body[2]
            qty = (bc // 2) if fc in (3, 4) else bc
            return {
                'kind': 'resp', 'slave': slave, 'fc': fc, 'type': name,
                'direction': 'read', 'addr': None, 'qty': qty,
            }
        return None

    if fc == 0x05 and len(body) >= 6:
        addr, _val = struct.unpack('>HH', body[2:6])
        return {
            'kind': 'req', 'slave': slave, 'fc': fc, 'type': 'coils',
            'direction': 'write', 'addr': addr, 'qty': 1,
        }

    if fc == 0x06 and len(body) >= 6:
        addr, _val = struct.unpack('>HH', body[2:6])
        return {
            'kind': 'req', 'slave': slave, 'fc': fc, 'type': 'holding',
            'direction': 'write', 'addr': addr, 'qty': 1,
        }

    if fc in (0x0F, 0x10):
        kind = 'coils' if fc == 0x0F else 'holding'
        if len(body) == 6:
            addr, qty = struct.unpack('>HH', body[2:6])
            return {
                'kind': 'resp', 'slave': slave, 'fc': fc, 'type': kind,
                'direction': 'write', 'addr': addr, 'qty': qty,
            }
        if len(body) >= 7:
            addr, qty, _bc = struct.unpack('>HHB', body[2:7])
            return {
                'kind': 'req', 'slave': slave, 'fc': fc, 'type': kind,
                'direction': 'write', 'addr': addr, 'qty': qty,
            }
        return None

    return None


def req_key(info: dict) -> tuple | None:
    """Ключ строки анализа: (slave, type, direction, addr, qty)."""
    if not info or info.get('addr') is None or info.get('qty') is None:
        return None
    if info.get('type') is None or info.get('direction') is None:
        return None
    return (info['slave'], info['type'], info['direction'], info['addr'], info['qty'])


def build_response(req: dict, values: list[int] | None = None) -> bytes:
    """Ответ слейва. values=None → нули. req — parse_pdu kind=req."""
    slave = req['slave']
    fc = req['fc']
    addr = req['addr']
    qty = req['qty']
    vals = list(values) if values is not None else [0] * qty

    if fc in (1, 2):
        if len(vals) < qty:
            vals = vals + [0] * (qty - len(vals))
        nbytes = (qty + 7) // 8
        bits = bytearray(nbytes)
        for i in range(qty):
            if int(vals[i]):
                bits[i // 8] |= 1 << (i % 8)
        body = bytes([slave, fc, nbytes]) + bytes(bits)
    elif fc in (3, 4):
        if len(vals) < qty:
            vals = vals + [0] * (qty - len(vals))
        payload = struct.pack('>' + 'H' * qty, *[int(v) & 0xFFFF for v in vals[:qty]])
        body = bytes([slave, fc, len(payload)]) + payload
    elif fc == 0x05:
        v = 0xFF00 if (vals and int(vals[0])) else 0x0000
        body = struct.pack('>BBHH', slave, fc, addr, v)
    elif fc == 0x06:
        v = int(vals[0]) & 0xFFFF if vals else 0
        body = struct.pack('>BBHH', slave, fc, addr, v)
    elif fc in (0x0F, 0x10):
        body = struct.pack('>BBHH', slave, fc, addr, qty)
    else:
        raise ValueError(f'unsupported FC 0x{fc:02X}')
    return append_crc(body)


def build_zero_response(req: dict) -> bytes:
    """Ответ нулями (обёртка)."""
    return build_response(req, None)


def decode_body(body: bytes):
    """body — кадр без CRC.

    Возвращает:
      * str — человекочитаемое описание кадра;
      * ('EXC', slave, base_fc, code) — для Modbus-исключений.
    """
    info = parse_pdu(body)
    if info is None:
        if len(body) < 2:
            return "short frame"
        return f"id={body[0]} fc=0x{body[1]:02X} data={body[2:].hex(' ')}"

    if info['kind'] == 'exc':
        return ('EXC', info['slave'], info['fc'], info['code'])

    name = info.get('type') or '?'
    slave = info['slave']
    if info['kind'] == 'req':
        if info['direction'] == 'read':
            return (f"REQ read {name} id={slave} "
                    f"addr={info['addr']} qty={info['qty']}")
        if info['fc'] == 0x05:
            return f"WR coil id={slave} addr={info['addr']}"
        if info['fc'] == 0x06:
            return f"WR reg id={slave} addr={info['addr']}"
        return (f"REQ write {name} id={slave} "
                f"addr={info['addr']} qty={info['qty']}")
    if info['direction'] == 'read':
        return f"RESP read {name} id={slave} qty={info['qty']}"
    return f"RESP write {name} id={slave} addr={info['addr']} qty={info['qty']}"


def format_exception(slave: int, base_fc: int, code: int, mode: str) -> str:
    """mode: 'code' | 'text' | 'both'."""
    txt = EXCEPTIONS.get(code, "?")
    if mode == 'code':
        return f"EXC id={slave} fc=0x{base_fc:02X} code={code}"
    if mode == 'text':
        return f"EXC id={slave} fc=0x{base_fc:02X} {txt}"
    return f"EXC id={slave} fc=0x{base_fc:02X} code={code} ({txt})"
