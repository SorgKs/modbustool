"""Разбор тела кадра Modbus RTU (без CRC)."""
import struct


EXCEPTIONS = {
    1: "illegal function", 2: "illegal data address", 3: "illegal data value",
    4: "slave device failure", 5: "acknowledge", 6: "slave device busy",
    7: "negative acknowledge", 8: "memory parity error",
    10: "gateway path unavailable", 11: "gateway target device failed",
}

READ_FC = {1: "coils", 2: "discrete", 3: "holding", 4: "input"}


def decode_body(body: bytes):
    """body — кадр без CRC.

    Возвращает:
      * str — человекочитаемое описание кадра;
      * ('EXC', slave, base_fc, code) — для Modbus-исключений.
    """
    if len(body) < 2:
        return "short frame"
    slave, fc = body[0], body[1]

    if fc & 0x80:
        base = fc & 0x7F
        code = body[2] if len(body) > 2 else -1
        return ('EXC', slave, base, code)

    if fc in READ_FC:
        name = READ_FC[fc]
        if len(body) == 6:
            addr, qty = struct.unpack('>HH', body[2:6])
            return f"REQ read {name} id={slave} addr={addr} qty={qty}"
        if len(body) >= 3:
            bc = body[2]
            data = body[3:3 + bc]
            if fc in (3, 4) and len(data) == bc and bc % 2 == 0:
                regs = struct.unpack('>' + 'H' * (bc // 2), data)
                return f"RESP read {name} id={slave} qty={bc // 2} [{' '.join(map(str, regs))}]"
            bits = ''.join(str((b >> i) & 1) for b in data for i in range(8))[:bc]
            return f"RESP read {name} id={slave} qty={bc} {bits}"
        return f"RESP read {name} id={slave} (incomplete)"

    if fc == 0x05 and len(body) >= 6:
        addr, val = struct.unpack('>HH', body[2:6])
        state = "on" if val == 0xFF00 else "off" if val == 0 else f"0x{val:04X}"
        return f"WR coil id={slave} addr={addr} {state}"

    if fc == 0x06 and len(body) >= 6:
        addr, val = struct.unpack('>HH', body[2:6])
        return f"WR reg id={slave} addr={addr} val={val}"

    if fc in (0x0F, 0x10):
        kind = "coils" if fc == 0x0F else "regs"
        if len(body) == 6:
            addr, qty = struct.unpack('>HH', body[2:6])
            return f"RESP write {kind} id={slave} addr={addr} qty={qty}"
        if len(body) >= 7:
            addr, qty, bc = struct.unpack('>HHB', body[2:7])
            payload = body[7:7 + bc]
            if fc == 0x10 and len(payload) == bc and bc % 2 == 0:
                regs = struct.unpack('>' + 'H' * (bc // 2), payload)
                return f"REQ write {kind} id={slave} addr={addr} qty={qty} [{' '.join(map(str, regs))}]"
            bits = ''.join(str((b >> i) & 1) for b in payload for i in range(8))[:qty]
            return f"REQ write {kind} id={slave} addr={addr} qty={qty} {bits}"
        return f"REQ write {kind} id={slave} (incomplete)"

    return f"id={slave} fc=0x{fc:02X} data={body[2:].hex(' ')}"


def format_exception(slave: int, base_fc: int, code: int, mode: str) -> str:
    """mode: 'code' | 'text' | 'both'."""
    txt = EXCEPTIONS.get(code, "?")
    if mode == 'code':
        return f"EXC id={slave} fc=0x{base_fc:02X} code={code}"
    if mode == 'text':
        return f"EXC id={slave} fc=0x{base_fc:02X} {txt}"
    return f"EXC id={slave} fc=0x{base_fc:02X} code={code} ({txt})"
