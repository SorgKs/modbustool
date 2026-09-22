"""Главное окно: тулбар, лог-панель, статус, обработка кадров."""
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import deque

import serial

from frame import Frame, classify
from logger import FrameLogger
from sniffer import SnifferWorker


PARITY_MAP = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
STOP_MAP = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}

PENDING_MAX = 5000


def compute_gap(baud: int, parity: str, stop: int) -> float:
    bits_per_char = 1 + 8 + stop + (0 if parity == 'N' else 1)
    return max(3.5 * bits_per_char / baud, 0.002)


class SnifferApp:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.ready = False
        root.title(f"Modbus RTU Sniffer — {cfg['port']} @ {cfg['baud']} 8{cfg['parity']}{cfg['stop']}")

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.paused = False
        self.pending = deque(maxlen=PENDING_MAX)
        self.logger = FrameLogger()
        self.good = 0
        self.bad = 0

        self.raw_var = tk.BooleanVar(value=True)
        self.inf_var = tk.BooleanVar(value=True)
        self.exc_var = tk.StringVar(value='both')
        self.autoscroll_var = tk.BooleanVar(value=True)

        self._build_ui()

        try:
            self.ser = serial.Serial(
                port=cfg['port'], baudrate=cfg['baud'], bytesize=8,
                parity=PARITY_MAP[cfg['parity']], stopbits=STOP_MAP[cfg['stop']],
                timeout=0.02,
            )
        except Exception as e:
            print(f"Не удалось открыть порт: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть порт", str(e))
            root.destroy()
            return

        self.worker = SnifferWorker(
            self.ser, self.q, self.stop_event,
            compute_gap(cfg['baud'], cfg['parity'], cfg['stop']),
        )
        self.worker.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind('<space>', lambda e: self.toggle_pause())
        self.root.bind('<Control-l>', lambda e: self.clear())

        self.root.after(50, self.poll)
        self.update_status()
        self.ready = True

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(6, 4))
        top.pack(fill='x')

        ttk.Label(top, text="Port:").pack(side='left')
        ttk.Label(top, text=self.cfg['port'], foreground='#036').pack(side='left', padx=(2, 10))

        self.pause_btn = ttk.Button(top, text="Pause", width=8, command=self.toggle_pause)
        self.pause_btn.pack(side='left', padx=2)

        ttk.Checkbutton(top, text="raw", variable=self.raw_var).pack(side='left', padx=4)
        ttk.Checkbutton(top, text="inf", variable=self.inf_var).pack(side='left', padx=4)
        ttk.Checkbutton(top, text="autoscroll", variable=self.autoscroll_var).pack(side='left', padx=4)

        ttk.Label(top, text="errors:").pack(side='left', padx=(10, 2))
        ttk.Combobox(top, textvariable=self.exc_var, width=6, state='readonly',
                     values=['code', 'text', 'both']).pack(side='left')

        ttk.Button(top, text="Clear", command=self.clear).pack(side='right', padx=2)
        self.log_btn = ttk.Button(top, text="Log file…", command=self.choose_log)
        self.log_btn.pack(side='right', padx=2)
        self.log_lbl = ttk.Label(top, text="(no log)", foreground='#666')
        self.log_lbl.pack(side='right', padx=4)

        mid = ttk.Frame(self.root)
        mid.pack(fill='both', expand=True, padx=6)

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
            messagebox.showerror("Ошибка порта", msg[1])
            self.on_close()

    def _process_frame(self, raw, t_end, prev_end):
        frame, is_good, self.bad = classify(raw, t_end, prev_end, self.bad)
        if is_good:
            self.good += 1
            self.update_status()
            return

        # лог — всегда полный
        self.logger.write_frame(frame)

        # отображение
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
        self.pause_btn.configure(text="Resume" if self.paused else "Pause")
        if not self.paused and self.pending:
            self.text.configure(state='normal')
            for frame in self.pending:
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
            self.pending.clear()
            if self.autoscroll_var.get():
                self.text.see('end')
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
            self.log_btn.configure(text="Log file…")
            self.log_lbl.configure(text="(no log)")
            return
        path = filedialog.asksaveasfilename(
            title="Файл лога", defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.logger.open(path)
            self.log_btn.configure(text="Stop log")
            self.log_lbl.configure(text=path)
        except Exception as e:
            print(f"Не удалось открыть лог: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть лог", str(e))

    def update_status(self):
        state = "PAUSED" if self.paused else "LIVE"
        queued = f"  queued={len(self.pending)}" if self.pending else ""
        log_state = "log:on" if self.logger.is_open() else "log:off"
        self.status_lbl.configure(
            text=f"{state}   good={self.good}  bad={self.bad}   {log_state}{queued}   "
                 f"[Space=pause  Ctrl+L=clear]"
        )

    # ---------- Закрытие ----------
    def on_close(self):
        try:
            self.stop_event.set()
            if hasattr(self, 'worker'):
                self.worker.join(timeout=1.0)
            if hasattr(self, 'ser') and self.ser.is_open:
                self.ser.close()
            self.logger.close()
        finally:
            self.root.destroy()
