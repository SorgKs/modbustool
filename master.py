"""Сборка Modbus-запросов мастера и транзакция request/response."""
import struct
import sys
import threading
import time
import traceback

from crc import append_crc
from framing import FrameAssembler


def default_poll():
    """Один опрос по умолчанию: read holding addr=1 qty=1."""
    return {
        'slave': 1,
        'type': 'holding',
        'direction': 'read',
        'addr': 1,
        'qty': 1,
        'values': [],
    }


def default_master_cfg():
    return {
        'timeout_ms': 1000,
        'interval_ms': 1000,
        'polls': [default_poll()],
    }


def resolve_fc(poll: dict) -> int:
    """Автовыбор FC по type/direction/qty."""
    kind = poll['type']
    direction = poll['direction']
    qty = int(poll['qty'])
    if kind == 'input' and direction == 'write':
        raise ValueError('input registers are read-only')
    if kind == 'coils':
        if direction == 'read':
            return 0x01
        return 0x05 if qty == 1 else 0x0F
    if kind == 'holding':
        if direction == 'read':
            return 0x03
        return 0x06 if qty == 1 else 0x10
    if kind == 'input' and direction == 'read':
        return 0x04
    raise ValueError(f"unsupported poll: {kind}/{direction}")


def build_request(poll: dict) -> bytes:
    """Собрать RTU-кадр (с CRC) из описания опроса."""
    slave = int(poll['slave'])
    addr = int(poll['addr'])
    qty = int(poll['qty'])
    values = list(poll.get('values') or [])
    fc = resolve_fc(poll)

    if fc in (0x01, 0x02, 0x03, 0x04):
        body = struct.pack('>BBHH', slave, fc, addr, qty)
    elif fc == 0x05:
        if len(values) != 1:
            raise ValueError('coil write needs 1 value (0/1)')
        val = 0xFF00 if int(values[0]) else 0x0000
        body = struct.pack('>BBHH', slave, fc, addr, val)
    elif fc == 0x06:
        if len(values) != 1:
            raise ValueError('register write needs 1 value')
        body = struct.pack('>BBHH', slave, fc, addr, int(values[0]) & 0xFFFF)
    elif fc == 0x0F:
        if len(values) != qty:
            raise ValueError(f'coil write needs {qty} values')
        nbytes = (qty + 7) // 8
        bits = bytearray(nbytes)
        for i, v in enumerate(values):
            if int(v):
                bits[i // 8] |= 1 << (i % 8)
        body = struct.pack('>BBHHB', slave, fc, addr, qty, nbytes) + bytes(bits)
    elif fc == 0x10:
        if len(values) != qty:
            raise ValueError(f'register write needs {qty} values')
        payload = struct.pack('>' + 'H' * qty, *[int(v) & 0xFFFF for v in values])
        body = struct.pack('>BBHHB', slave, fc, addr, qty, len(payload)) + payload
    else:
        raise ValueError(f'unsupported FC 0x{fc:02X}')

    return append_crc(body)


def transact(ser, req: bytes, gap: float, timeout: float):
    """Отправить запрос, дождаться одного кадра ответа или timeout → None."""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(req)
    ser.flush()

    assembler = FrameAssembler(gap)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        now = time.monotonic()
        frames = assembler.feed(chunk, now)
        if frames:
            return frames[0][0]
        # дать gap сработать на пустом чтении
        if not chunk:
            frames = assembler.feed(b'', now)
            if frames:
                return frames[0][0]
        time.sleep(0.001)
    return None


class MasterPoller(threading.Thread):
    """Цикл опросов мастера → очередь UI.

    Сообщения:
      ('tx', raw, t_end, prev_end)
      ('frame', raw, t_end, prev_end)
      ('timeout', t_end, prev_end)
      ('poll_done',) — конец однократного прохода
      ('error', message)
    """

    def __init__(self, ser, out_queue, stop_event, cfg: dict, gap: float,
                 once: bool = False):
        super().__init__(daemon=True)
        self.ser = ser
        self.q = out_queue
        self.stop_event = stop_event
        self.cfg = cfg
        self.gap = gap
        self.once = once

    def run(self):
        try:
            timeout = max(self.cfg.get('timeout_ms', 1000), 1) / 1000.0
            interval = max(self.cfg.get('interval_ms', 1000), 0) / 1000.0
            polls = self.cfg.get('polls') or []
            prev = 0.0  # конец предыдущего пакета (TX/RX/TIMEOUT)
            while not self.stop_event.is_set():
                for poll in polls:
                    if self.stop_event.is_set():
                        return
                    req = build_request(poll)
                    t_tx = time.monotonic()
                    self.q.put(('tx', req, t_tx, prev))
                    resp = transact(self.ser, req, self.gap, timeout)
                    t_rx = time.monotonic()
                    if resp is None:
                        self.q.put(('timeout', t_rx, t_tx))
                    else:
                        self.q.put(('frame', resp, t_rx, t_tx))
                    prev = t_rx
                if self.once:
                    self.q.put(('poll_done',))
                    return
                # пауза между проходами списка
                end = time.monotonic() + interval
                while time.monotonic() < end:
                    if self.stop_event.is_set():
                        return
                    time.sleep(0.02)
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            self.q.put(('error', str(e)))
