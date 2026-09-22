"""Поток-читатель serial + сборка кадров."""
import sys
import threading
import time
import traceback

from framing import FrameAssembler


class SnifferWorker(threading.Thread):
    """Читает serial в отдельном потоке, отдаёт кадры в очередь.

    Сообщения в очереди:
      ('frame', raw_bytes, t_end, prev_end)
      ('error', message)
    """

    def __init__(self, ser, out_queue, stop_event, gap_seconds: float):
        super().__init__(daemon=True)
        self.ser = ser
        self.q = out_queue
        self.stop_event = stop_event
        self.assembler = FrameAssembler(gap_seconds)

    def run(self):
        try:
            while not self.stop_event.is_set():
                chunk = self.ser.read(256)
                now = time.monotonic()
                for raw, t_end, prev_end in self.assembler.feed(chunk, now):
                    self.q.put(('frame', raw, t_end, prev_end))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            self.q.put(('error', str(e)))
