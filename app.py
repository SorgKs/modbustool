"""Главное окно: меню, лог-панель, статус, обработка кадров."""
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import deque

import serial

from frame import Frame, classify
from logger import FrameLogger
from settings_dialog import SettingsDialog
from sniffer import SnifferWorker


PARITY_MAP = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
STOP_MAP = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}

PENDING_MAX = 5000


def compute_gap(baud: int, parity: str, stop: int) -> float:
    bits_per_char = 1 + 8 + stop + (0 if parity == 'N' else 1)
    return max(3.5 * bits_per_char / baud, 0.002)


class SnifferApp:
    def __init__(self, root: tk.Tk, cfg: dict | None = None):
        self.root = root
        self.cfg = cfg
        self.ready = False
        self.ser = None
        self.worker = None

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.paused = True  # старт на паузе
        self.pending = deque(maxlen=PENDING_MAX)
        self.logger = FrameLogger()
        self.good = 0
        self.bad = 0

        self.raw_var = tk.BooleanVar(value=True)
        self.inf_var = tk.BooleanVar(value=True)
        self.exc_var = tk.StringVar(value='both')
        self.autoscroll_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._bind_keys()
        self._update_title()

        # порт открывается только из настроек, не при старте
        if cfg is not None and not self._open_port(cfg):
            root.destroy()
            return

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self.poll)
        self.update_status()
        self.ready = True

    # ---------- UI ----------
    def _build_ui(self):
        self._build_menu()

        mid = ttk.Frame(self.root)
        mid.pack(fill='both', expand=True, padx=6, pady=(4, 0))

        self.text = tk.Text(mid, wrap='none', font=('Consolas', 10), undo=False,
                            background='#fbfbfb', foreground='#111')
        vsb = ttk.Scrollbar(mid, orient='vertical', command=self.text.yview)
        hsb = ttk.Scrollbar(mid, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        self.text.tag_configure('head', foreground='#c00', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('noise', foreground='#a50', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('raw', foreground='#555')
        self.text.tag_configure('inf', foreground='#005')
        self.text.configure(state='disabled')

        status = ttk.Frame(self.root, padding=(6, 2))
        status.pack(fill='x')
        self.status_lbl = ttk.Label(status, text="", anchor='w')
        self.status_lbl.pack(side='left', fill='x', expand=True)

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Настройки порта…", command=self.open_settings,
                           accelerator="P")
        m_file.add_separator()
        self.log_menu_label = "Файл лога…"
        m_file.add_command(label=self.log_menu_label, command=self.choose_log,
                           accelerator="O")
        self._log_menu = m_file
        self._log_menu_index = 2
        m_file.add_separator()
        m_file.add_command(label="Выход", command=self.on_close)
        menubar.add_cascade(label="Файл", menu=m_file)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_checkbutton(label="Raw", variable=self.raw_var, accelerator="R")
        m_view.add_checkbutton(label="Inf", variable=self.inf_var, accelerator="I")
        m_view.add_checkbutton(label="Autoscroll", variable=self.autoscroll_var,
                               accelerator="A")
        m_err = tk.Menu(m_view, tearoff=0)
        for mode, accel in (('code', '1'), ('text', '2'), ('both', '3')):
            m_err.add_radiobutton(label=mode, variable=self.exc_var, value=mode,
                                  accelerator=accel)
        m_view.add_cascade(label="Errors", menu=m_err)
        menubar.add_cascade(label="Вид", menu=m_view)

        m_cap = tk.Menu(menubar, tearoff=0)
        m_cap.add_command(label="Pause / Resume", command=self.toggle_pause,
                          accelerator="Space")
        m_cap.add_command(label="Clear", command=self.clear, accelerator="C")
        menubar.add_cascade(label="Захват", menu=m_cap)

        self.root.config(menu=menubar)

    def _bind_keys(self):
        # одиночные клавиши без модификаторов
        self.root.bind('<space>', lambda e: self.toggle_pause())
        self.root.bind('c', lambda e: self.clear())
        self.root.bind('C', lambda e: self.clear())
        self.root.bind('p', lambda e: self.open_settings())
        self.root.bind('P', lambda e: self.open_settings())
        self.root.bind('r', lambda e: self._toggle_bool(self.raw_var))
        self.root.bind('R', lambda e: self._toggle_bool(self.raw_var))
        self.root.bind('i', lambda e: self._toggle_bool(self.inf_var))
        self.root.bind('I', lambda e: self._toggle_bool(self.inf_var))
        self.root.bind('a', lambda e: self._toggle_bool(self.autoscroll_var))
        self.root.bind('A', lambda e: self._toggle_bool(self.autoscroll_var))
        self.root.bind('o', lambda e: self.choose_log())
        self.root.bind('O', lambda e: self.choose_log())
        self.root.bind('1', lambda e: self.exc_var.set('code'))
        self.root.bind('2', lambda e: self.exc_var.set('text'))
        self.root.bind('3', lambda e: self.exc_var.set('both'))

    @staticmethod
    def _toggle_bool(var: tk.BooleanVar):
        var.set(not var.get())

    def _update_title(self):
        c = self.cfg
        if c is None:
            self.root.title("Modbus RTU Sniffer")
            return
        self.root.title(
            f"Modbus RTU Sniffer — {c['port']} @ {c['baud']} 8{c['parity']}{c['stop']}"
        )

    # ---------- Порт ----------
    def _open_port(self, cfg: dict) -> bool:
        """Открыть serial и запустить worker. False при ошибке."""
        try:
            ser = serial.Serial(
                port=cfg['port'], baudrate=cfg['baud'], bytesize=8,
                parity=PARITY_MAP[cfg['parity']], stopbits=STOP_MAP[cfg['stop']],
                timeout=0.02,
            )
        except Exception as e:
            print(f"Не удалось открыть порт: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть порт", str(e), parent=self.root)
            return False

        self.stop_event = threading.Event()
        self.ser = ser
        self.cfg = cfg
        self.worker = SnifferWorker(
            self.ser, self.q, self.stop_event,
            compute_gap(cfg['baud'], cfg['parity'], cfg['stop']),
        )
        self.worker.start()
        self._update_title()
        return True

    def _close_port(self):
        """Остановить worker и закрыть serial."""
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=1.0)
            self.worker = None
        if self.ser is not None and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def open_settings(self):
        """Диалог настроек порта; при OK — подключение/переподключение."""
        dlg = SettingsDialog(self.root, initial=self.cfg)
        result = dlg.wait()
        if result is None or result == self.cfg:
            return
        self._close_port()
        if not self._open_port(result):
            self.cfg = None
            self._update_title()
            messagebox.showwarning(
                "Порт закрыт",
                "Не удалось открыть порт. Откройте настройки снова.",
                parent=self.root,
            )
        self.update_status()

    # ---------- Очередь ----------
    def poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == 'frame':
            _, raw, t_end, prev_end = msg
            self._process_frame(raw, t_end, prev_end)
        elif kind == 'error':
            print(f"Ошибка порта: {msg[1]}", file=sys.stderr, flush=True)
            messagebox.showerror("Ошибка порта", msg[1], parent=self.root)
            self.on_close()

    def _process_frame(self, raw, t_end, prev_end):
        frame, is_good, self.bad = classify(raw, t_end, prev_end, self.bad)
        if is_good:
            self.good += 1
            self.update_status()
            return

        # лог — всегда полный
        self.logger.write_frame(frame)

        if self.paused:
            self.pending.append(frame)
            self.update_status()
            return
        self._render_frame(frame)

    # ---------- Рендер ----------
    def _render_frame(self, frame: Frame):
        at_bottom = self.text.yview()[1] >= 0.999
        self.text.configure(state='normal')
        self.text.insert('end', frame.header() + '\n',
                         'noise' if frame.is_noise else 'head')
        if self.raw_var.get():
            r = frame.raw_line()
            if r:
                self.text.insert('end', r + '\n', 'raw')
        if self.inf_var.get():
            i = frame.inf_line(self.exc_var.get())
            if i:
                self.text.insert('end', i + '\n', 'inf')
        self.text.configure(state='disabled')
        if self.autoscroll_var.get() or at_bottom:
            self.text.see('end')

    # ---------- Действия ----------
    def toggle_pause(self):
        self.paused = not self.paused
        if not self.paused and self.pending:
            for frame in self.pending:
                self._render_frame(frame)
            self.pending.clear()
        self.update_status()

    def clear(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
        self.pending.clear()
        self.update_status()

    def choose_log(self):
        if self.logger.is_open():
            self.logger.close()
            self._log_menu.entryconfigure(self._log_menu_index, label="Файл лога…")
            self.update_status()
            return
        path = filedialog.asksaveasfilename(
            title="Файл лога", defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        try:
            self.logger.open(path)
            self._log_menu.entryconfigure(self._log_menu_index, label="Остановить лог")
            self.update_status()
        except Exception as e:
            print(f"Не удалось открыть лог: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть лог", str(e), parent=self.root)

    def update_status(self):
        state = "PAUSED" if self.paused else "LIVE"
        queued = f"  queued={len(self.pending)}" if self.pending else ""
        log_state = "log:on" if self.logger.is_open() else "log:off"
        c = self.cfg
        port = f"{c['port']}@{c['baud']}" if c else "no port"
        self.status_lbl.configure(
            text=f"{state}  {port}  good={self.good}  bad={self.bad}  "
                 f"{log_state}{queued}  "
                 f"[Space pause · C clear · P port · O log · R/I/A view · 1/2/3 err]"
        )

    # ---------- Закрытие ----------
    def on_close(self):
        try:
            self._close_port()
            self.logger.close()
        finally:
            self.root.destroy()
