"""Эмулятор слейва: ответ в порт + TX в очередь (лог/экран)."""
import sys
import threading
import time
import traceback

from framing import FrameAssembler
from protocol import build_response, parse_pdu, req_key


class SlaveEmu(threading.Thread):
    """Читает serial, на emu_keys пишет response в порт и шлёт ('tx',…) в UI.

    emu_keys: set[key]
    emu_data: dict[key, list[int]] — нет ключа → нули
    gap: порог RX (1.5 символа); перед TX ждём ≥ 3.5 символа
    """

    def __init__(self, ser, out_queue, stop_event, gap: float,
                 emu_keys: set, emu_data: dict):
        super().__init__(daemon=True)
        self.ser = ser
        self.q = out_queue
        self.stop_event = stop_event
        self.gap = gap
        self.tx_gap = gap * (3.5 / 1.5)
        self.emu_keys = emu_keys
        self.emu_data = emu_data
        self.assembler = FrameAssembler(gap)
        self._prev_end = 0.0

    def run(self):
        try:
            while not self.stop_event.is_set():
                chunk = self.ser.read(256)
                now = time.monotonic()
                frames = self.assembler.feed(chunk, now)
                for raw, t_end, prev_end, tight in frames:
                    self._prev_end = t_end
                    self.q.put(('frame', raw, t_end, prev_end, tight))
                    self._maybe_reply(raw)
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            self.q.put(('error', str(e)))

    def _write_resp(self, resp: bytes) -> None:
        """Запись с повторами при кратком write-timeout."""
        deadline = time.monotonic() + 2.0
        view = memoryview(resp)
        while len(view):
            if self.stop_event.is_set():
                raise OSError('aborted')
            try:
                n = self.ser.write(view)
                if not n:
                    time.sleep(0.005)
                    continue
                view = view[n:]
            except Exception as e:
                if time.monotonic() >= deadline:
                    raise
                # типичный short timeout socket — подождать и повторить
                if 'timeout' not in str(e).lower() and 'timed' not in str(e).lower():
                    raise
                time.sleep(0.01)
        try:
            self.ser.flush()
        except Exception:
            pass

    def _maybe_reply(self, raw: bytes):
        if len(raw) < 4:
            return
        body = raw[:-2]
        info = parse_pdu(body)
        if not info or info.get('kind') != 'req':
            return
        key = req_key(info)
        if key is None or key not in self.emu_keys:
            return
        try:
            vals = self.emu_data.get(key)
            resp = build_response(info, vals)
            time.sleep(self.tx_gap)
            if self.stop_event.is_set():
                return
            self._write_resp(resp)
            t_tx = time.monotonic()
            self.q.put(('tx', resp, t_tx, self._prev_end))
            self._prev_end = t_tx
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            # не рвём сессию — только сообщение в лог-очередь как tx-ошибка
            self.q.put(('error', f'emu TX: {e}'))
