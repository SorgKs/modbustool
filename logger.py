"""Файловый лог. Пишет полный набор строк независимо от UI."""


class FrameLogger:
    def __init__(self):
        self._f = None
        self._path = None

    @property
    def path(self):
        return self._path

    def is_open(self) -> bool:
        return self._f is not None

    def open(self, path: str):
        self.close()
        self._f = open(path, 'a', encoding='utf-8')
        self._path = path

    def close(self):
        if self._f:
            try:
                self._f.close()
            finally:
                self._f = None
                self._path = None

    def write_frame(self, frame):
        if not self._f:
            return
        for line in frame.log_lines():
            self._f.write(line + '\n')
        self._f.flush()
