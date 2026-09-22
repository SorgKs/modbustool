"""Сборка кадров Modbus RTU по межсимвольным паузам."""


class FrameAssembler:
    """Копит байты, отдаёт готовые кадры, разделяя их паузой >= gap."""

    def __init__(self, gap_seconds: float):
        self.gap = gap_seconds
        self._buf = bytearray()
        self._last_rx = 0.0
        self._prev_end = 0.0

    def feed(self, chunk: bytes, now: float):
        """chunk — что пришло с порта (может быть пустым).
        now — time.monotonic() на момент чтения.

        Возвращает список кортежей (frame_bytes, t_end, prev_end).
        """
        out = []
        if chunk:
            if self._buf and (now - self._last_rx) > self.gap:
                out.append(self._flush())
            self._buf.extend(chunk)
            self._last_rx = now
        else:
            if self._buf and (now - self._last_rx) > self.gap:
                out.append(self._flush())
        return out

    def _flush(self):
        frame = bytes(self._buf)
        t = self._last_rx
        prev = self._prev_end
        self._prev_end = t
        self._buf.clear()
        return frame, t, prev
