"""Общий I/O: COM / URL / TCP-сервер для mbgen и modbustool."""
import socket
import threading
import time

import serial
import serial.tools.list_ports


PARITY_MAP = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
STOP_MAP = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}


def char_time(baud: int, parity: str, stop: int) -> float:
    """Длительность одного символа на линии, сек."""
    bits_per_char = 1 + 8 + stop + (0 if parity == 'N' else 1)
    return bits_per_char / float(baud)


def compute_gap(baud: int, parity: str, stop: int) -> float:
    """Межкадровая пауза TX: 3.5 символа (минимум для RTU)."""
    return max(3.5 * char_time(baud, parity, stop), 0.002)


def compute_rx_gap(baud: int, parity: str, stop: int) -> float:
    """Конец кадра на приёме: пауза > 1.5 символа."""
    return max(1.5 * char_time(baud, parity, stop), 0.001)


def is_url_port(port: str) -> bool:
    return '://' in (port or '')


def open_serial(port: str, baud: int, parity: str, stop: int,
                timeout: float | None = None,
                inter_byte_timeout: float | None = None,
                write_timeout: float | None = 2.0):
    """COM через Serial, URL (socket://…) через serial_for_url.

    inter_byte_timeout по умолчанию = 1.5 символа — read не добирает
    следующий кадр в тот же вызов (на обычном COM).
    write_timeout — отдельно; иначе короткий read-timeout душит TX (emu).
    """
    rx = compute_rx_gap(baud, parity, stop)
    if inter_byte_timeout is None:
        inter_byte_timeout = rx
    if timeout is None:
        # URL: короткий poll; COM: ждать первый байт дольше
        timeout = max(rx * 0.5, 0.005) if is_url_port(port) else max(rx * 3, 0.01)

    kwargs = dict(
        baudrate=baud, bytesize=8,
        parity=PARITY_MAP[parity], stopbits=STOP_MAP[stop],
        timeout=timeout,
        write_timeout=write_timeout,
    )
    if is_url_port(port):
        # serial_for_url может игнорировать write_timeout в kwargs
        ser = serial.serial_for_url(port, **{k: v for k, v in kwargs.items()
                                            if k != 'write_timeout'})
        try:
            ser.write_timeout = write_timeout
        except Exception:
            pass
        return ser
    kwargs['inter_byte_timeout'] = inter_byte_timeout
    return serial.Serial(port=port, **kwargs)


def list_com_ports():
    """Список (devices, labels) для Combobox."""
    devices, labels = [], []
    for p in serial.tools.list_ports.comports():
        desc = p.description if p.description and p.description != 'n/a' else ''
        devices.append(p.device)
        labels.append(f"{p.device}  —  {desc}" if desc else p.device)
    if not labels:
        devices = ['']
        labels = ['(порты не найдены)']
    return devices, labels


class TcpByteServer:
    """TCP listen, одно клиентское соединение; API как у Serial: write/read/flush/close."""

    def __init__(self, host: str = '127.0.0.1', port: int = 9502):
        self.host = host
        self.port = port
        self._sock = None
        self._conn = None
        self._lock = threading.Lock()
        self._closed = False
        self._accept_thread = None

    def start(self):
        """Слушать порт; клиент подключается в фоне."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(1)
        self._sock.settimeout(1.0)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self):
        while not self._closed:
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except OSError:
                        pass
                self._conn = conn
                self._conn.settimeout(0.02)
                self._conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def write(self, data: bytes, on_block=None, should_abort=None) -> int:
        """Отправка; при переполнении TX-буфера ждёт (on_block один раз)."""
        mv = memoryview(data)
        notified = False
        while len(mv):
            if self._closed or (should_abort and should_abort()):
                raise OSError('aborted')
            with self._lock:
                conn = self._conn
            if conn is None:
                raise OSError('no TCP client connected')
            try:
                n = conn.send(mv)
                if n == 0:
                    raise OSError('connection closed')
                mv = mv[n:]
            except socket.timeout:
                if on_block and not notified:
                    on_block()
                    notified = True
                time.sleep(0.01)
        return len(data)

    def read(self, n: int = 256) -> bytes:
        with self._lock:
            conn = self._conn
        if conn is None:
            return b''
        try:
            return conn.recv(n)
        except (socket.timeout, OSError):
            return b''

    def flush(self):
        pass

    def close(self):
        self._closed = True
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._conn is not None
