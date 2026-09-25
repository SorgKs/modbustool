"""Файловый лог. Пишет полный набор строк независимо от UI."""
import os
import time
from pathlib import Path

from logformat import format_marker


def default_logs_dir() -> Path:
    """Каталог логов: %LOCALAPPDATA%\\modbustool\\logs (Windows) или ~/.local/share/..."""
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME')
    if base:
        root = Path(base) / 'modbustool' / 'logs'
    else:
        root = Path.home() / '.local' / 'share' / 'modbustool' / 'logs'
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_session_log_path() -> Path:
    """Новый файл на сессию: YYYYMMDD-HHMMSS.log."""
    stamp = time.strftime('%Y%m%d-%H%M%S')
    return default_logs_dir() / f'{stamp}.log'


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
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, 'a', encoding='utf-8')
        self._path = path
        self._f.write(f'--- session {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n')
        self._f.write(format_marker() + '\n')
        self._f.flush()

    def open_default_session(self) -> str:
        """Открыть лог сессии в стандартном каталоге."""
        path = str(default_session_log_path())
        self.open(path)
        return path

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
