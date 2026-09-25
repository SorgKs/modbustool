"""Главное окно: меню, лог-панель / таблица анализа, monitor/master/analyze."""
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from collections import deque

from analyze import analyze_frames, analyze_log_file
from emu_data_dialog import EmuDataDialog
from frame import Frame, classify
from logger import FrameLogger, default_logs_dir
from master import MasterPoller, default_master_cfg
from master_dialog import MasterDialog
from serial_io import compute_rx_gap, open_serial
from settings_dialog import SettingsDialog
from slave_emu import SlaveEmu
from sniffer import SnifferWorker


PENDING_MAX = 5000

ANALYZE_COLS = (
    'emu', 'edit', 'slave', 'type', 'dir', 'addr', 'qty',
    'req_ok', 'req_bad', 'resp_ok', 'resp_bad', 'resp_miss',
    'period', 'status',
)


def _key_str(key: tuple) -> str:
    return f"{key[0]}|{key[1]}|{key[2]}|{key[3]}|{key[4]}"


def _parse_key(s: str) -> tuple:
    a, b, c, d, e = s.split('|')
    return (int(a), b, c, int(d), int(e))


class SnifferApp:
    def __init__(self, root: tk.Tk, cfg: dict | None = None):
        self.root = root
        self.cfg = cfg
        self.ready = False
        self.ser = None
        self.worker = None

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.paused = True
        self.history = deque(maxlen=PENDING_MAX)
        self.logger = FrameLogger()
        self.logger.open_default_session()
        self.good = 0
        self.bad = 0

        self.mode_var = tk.StringVar(value='monitor')
        self.master_cfg = default_master_cfg()
        self._master_once = False
        self.emu_keys: set[tuple] = set()
        self.emu_data: dict[tuple, list[int]] = {}
        self._analyze_source: str | None = None  # путь лога или None=history
        self._analyze_rows: list[dict] = []
        self._analyze_refresh_after = None  # id after() для throttle

        self.raw_var = tk.BooleanVar(value=True)
        self.inf_var = tk.BooleanVar(value=True)
        self.exc_var = tk.StringVar(value='both')
        self.autoscroll_var = tk.BooleanVar(value=True)
        self.raw_base_var = tk.StringVar(value='hex')

        self._build_ui()
        self._bind_keys()
        self._watch_view()
        self._update_title()
        self._update_mode_menus()

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

        # статус снизу — как в мониторе, всегда виден
        status = ttk.Frame(self.root, padding=(6, 2))
        status.pack(side='bottom', fill='x')
        self.status_lbl = ttk.Label(status, text="", anchor='w')
        self.status_lbl.pack(side='left', fill='x', expand=True)

        self.mid = ttk.Frame(self.root)
        self.mid.pack(fill='both', expand=True, padx=6, pady=(4, 0))
        self.mid.rowconfigure(0, weight=1)
        self.mid.columnconfigure(0, weight=1)

        # вертикальный сплит: сверху таблица (analyze), снизу лог
        self.paned = ttk.Panedwindow(self.mid, orient='vertical')
        self.paned.grid(row=0, column=0, sticky='nsew')

        self.analyze_frame = ttk.Frame(self.paned)
        self.analyze_frame.rowconfigure(0, weight=1)
        self.analyze_frame.columnconfigure(0, weight=1)

        self.log_frame = ttk.Frame(self.paned)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(self.log_frame, wrap='none', font=('Consolas', 10),
                            undo=False, background='#fbfbfb', foreground='#111')
        vsb = ttk.Scrollbar(self.log_frame, orient='vertical', command=self.text.yview)
        hsb = ttk.Scrollbar(self.log_frame, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        self.text.tag_configure('head', foreground='#c00', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('ok', foreground='#080', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('gap', foreground='#a60', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('tx', foreground='#06c', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('timeout', foreground='#a50', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('noise', foreground='#a50', font=('Consolas', 10, 'bold'))
        self.text.tag_configure('raw', foreground='#555')
        self.text.tag_configure('inf', foreground='#005')
        self.text.configure(state='disabled')

        self.tree = ttk.Treeview(
            self.analyze_frame, columns=ANALYZE_COLS, show='headings',
            selectmode='browse',
        )
        headings = {
            'emu': 'emu', 'edit': 'edit', 'slave': 'slave', 'type': 'type',
            'dir': 'dir', 'addr': 'addr', 'qty': 'qty',
            'req_ok': 'req_ok', 'req_bad': 'req_bad',
            'resp_ok': 'resp_ok', 'resp_bad': 'resp_bad', 'resp_miss': 'resp_miss',
            'period': 'period', 'status': 'status',
        }
        widths = {
            'emu': 40, 'edit': 50, 'slave': 50, 'type': 70, 'dir': 50,
            'addr': 60, 'qty': 50,
            'req_ok': 60, 'req_bad': 60, 'resp_ok': 60, 'resp_bad': 70,
            'resp_miss': 70, 'period': 70, 'status': 80,
        }
        for col in ANALYZE_COLS:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor='center', stretch=True)
        tvsb = ttk.Scrollbar(self.analyze_frame, orient='vertical',
                             command=self.tree.yview)
        self.tree.configure(yscrollcommand=tvsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        tvsb.grid(row=0, column=1, sticky='ns')
        self.tree.bind('<Button-1>', self._on_tree_click)

        # по умолчанию только лог (monitor)
        self.paned.add(self.log_frame, weight=1)

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Настройки порта…", command=self.open_settings,
                           accelerator="P")
        m_file.add_separator()
        m_file.add_command(label="Сменить файл лога…", command=self.choose_log,
                           accelerator="O")
        m_file.add_command(label="Открыть папку логов", command=self.open_logs_dir)
        m_file.add_separator()
        m_file.add_command(label="Выход", command=self.on_close)
        menubar.add_cascade(label="Файл", menu=m_file)

        m_mode = tk.Menu(menubar, tearoff=0)
        m_mode.add_radiobutton(label="Монитор", variable=self.mode_var, value='monitor',
                               command=self._on_mode_change)
        m_mode.add_radiobutton(label="Мастер", variable=self.mode_var, value='master',
                               command=self._on_mode_change)
        m_mode.add_radiobutton(label="Анализ", variable=self.mode_var, value='analyze',
                               command=self._on_mode_change)
        menubar.add_cascade(label="Режим", menu=m_mode)

        self._m_master = tk.Menu(menubar, tearoff=0)
        self._m_master.add_command(label="Настройка опроса…",
                                   command=self.open_master_settings, accelerator="M")
        self._m_master.add_command(label="Отправить раз",
                                   command=self.master_send_once, accelerator="S")
        self._m_master.add_command(label="Старт / Стоп опроса",
                                   command=self.toggle_pause, accelerator="Space")
        menubar.add_cascade(label="Мастер", menu=self._m_master)

        self._m_analyze = tk.Menu(menubar, tearoff=0)
        self._m_analyze.add_command(label="Открыть лог…",
                                    command=self.analyze_open_log)
        self._m_analyze.add_command(label="Обновить",
                                    command=self.analyze_refresh, accelerator="F5")
        menubar.add_cascade(label="Анализ", menu=self._m_analyze)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_checkbutton(label="Raw", variable=self.raw_var, accelerator="R")
        m_view.add_checkbutton(label="Inf", variable=self.inf_var, accelerator="I")
        m_view.add_checkbutton(label="Autoscroll", variable=self.autoscroll_var,
                               accelerator="A")
        m_raw = tk.Menu(m_view, tearoff=0)
        m_raw.add_radiobutton(label="hex", variable=self.raw_base_var, value='hex',
                              accelerator="H")
        m_raw.add_radiobutton(label="dec", variable=self.raw_base_var, value='dec',
                              accelerator="H")
        m_view.add_cascade(label="Raw digits", menu=m_raw)
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

    def _update_mode_menus(self):
        mode = self.mode_var.get()
        m_state = 'normal' if mode == 'master' else 'disabled'
        for i in range(3):
            self._m_master.entryconfigure(i, state=m_state)
        a_state = 'normal' if mode == 'analyze' else 'disabled'
        for i in range(2):
            self._m_analyze.entryconfigure(i, state=a_state)

    def _bind_keys(self):
        self.root.bind('<space>', lambda e: self.toggle_pause())
        self.root.bind('<F5>', lambda e: self.analyze_refresh())
        self.root.bind('c', lambda e: self.clear())
        self.root.bind('C', lambda e: self.clear())
        self.root.bind('p', lambda e: self.open_settings())
        self.root.bind('P', lambda e: self.open_settings())
        self.root.bind('m', lambda e: self.open_master_settings())
        self.root.bind('M', lambda e: self.open_master_settings())
        self.root.bind('s', lambda e: self.master_send_once())
        self.root.bind('S', lambda e: self.master_send_once())
        self.root.bind('r', lambda e: self._toggle_bool(self.raw_var))
        self.root.bind('R', lambda e: self._toggle_bool(self.raw_var))
        self.root.bind('i', lambda e: self._toggle_bool(self.inf_var))
        self.root.bind('I', lambda e: self._toggle_bool(self.inf_var))
        self.root.bind('a', lambda e: self._toggle_bool(self.autoscroll_var))
        self.root.bind('A', lambda e: self._toggle_bool(self.autoscroll_var))
        self.root.bind('o', lambda e: self.choose_log())
        self.root.bind('O', lambda e: self.choose_log())
        self.root.bind('h', lambda e: self._toggle_raw_base())
        self.root.bind('H', lambda e: self._toggle_raw_base())
        self.root.bind('1', lambda e: self.exc_var.set('code'))
        self.root.bind('2', lambda e: self.exc_var.set('text'))
        self.root.bind('3', lambda e: self.exc_var.set('both'))

    @staticmethod
    def _toggle_bool(var: tk.BooleanVar):
        var.set(not var.get())

    def _toggle_raw_base(self):
        cur = self.raw_base_var.get()
        self.raw_base_var.set('dec' if cur == 'hex' else 'hex')

    def _watch_view(self):
        for var in (self.raw_var, self.inf_var, self.exc_var, self.raw_base_var):
            var.trace_add('write', lambda *_: self._refresh_display())

    def _update_title(self):
        mode = self.mode_var.get()
        labels = {'monitor': 'Monitor', 'master': 'Master', 'analyze': 'Analyze'}
        mode_lbl = labels.get(mode, mode)
        c = self.cfg
        if c is None:
            self.root.title(f"Modbus RTU — {mode_lbl}")
            return
        self.root.title(
            f"Modbus RTU — {mode_lbl} — {c['port']} @ {c['baud']} "
            f"8{c['parity']}{c['stop']}"
        )

    def _show_analyze_view(self, show: bool):
        # сверху таблица, снизу лог — или только лог
        panes = self.paned.panes()
        if show:
            if str(self.analyze_frame) not in panes:
                self.paned.insert(0, self.analyze_frame, weight=1)
            if str(self.log_frame) not in panes:
                self.paned.add(self.log_frame, weight=1)
        else:
            if str(self.analyze_frame) in panes:
                self.paned.forget(self.analyze_frame)
            if str(self.log_frame) not in panes:
                self.paned.add(self.log_frame, weight=1)

    # ---------- Режим ----------
    def _on_mode_change(self):
        self._stop_worker()
        self.paused = True
        self._master_once = False
        mode = self.mode_var.get()
        self._show_analyze_view(mode == 'analyze')
        self._update_title()
        self._update_mode_menus()
        if mode == 'analyze':
            self._analyze_source = None
            self.paused = True
            self.analyze_refresh()
            self._refresh_display()
            self._ensure_analyze_worker()
        else:
            self._stop_worker()
        self.update_status()

    # ---------- Порт ----------
    def _open_port(self, cfg: dict) -> bool:
        try:
            ser = open_serial(
                cfg['port'], cfg['baud'], cfg['parity'], cfg['stop'],
            )
        except Exception as e:
            print(f"Не удалось открыть порт: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть порт", str(e), parent=self.root)
            return False

        self.ser = ser
        self.cfg = cfg
        self._update_title()
        return True

    def _gap(self) -> float:
        """Порог конца кадра на приёме (1.5 символа)."""
        c = self.cfg
        return compute_rx_gap(c['baud'], c['parity'], c['stop'])

    def _stop_worker(self):
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=2.0)
            self.worker = None
        self.stop_event = threading.Event()

    def _start_sniffer(self):
        if self.ser is None or self.worker is not None:
            return
        self.stop_event = threading.Event()
        self.worker = SnifferWorker(self.ser, self.q, self.stop_event, self._gap())
        self.worker.start()

    def _start_master_poller(self, once: bool):
        if self.ser is None or self.worker is not None:
            return
        self.stop_event = threading.Event()
        self._master_once = once
        self.worker = MasterPoller(
            self.ser, self.q, self.stop_event, self.master_cfg, self._gap(),
            once=once,
        )
        self.worker.start()

    def _ensure_analyze_worker(self):
        """В анализе: Space LIVE → sniffer или SlaveEmu; PAUSED → стоп."""
        if self.mode_var.get() != 'analyze':
            return
        if self.paused or self.ser is None:
            if self.worker is not None:
                self._stop_worker()
            self.update_status()
            return
        want_emu = bool(self.emu_keys)
        is_emu = isinstance(self.worker, SlaveEmu)
        is_sniff = isinstance(self.worker, SnifferWorker)
        if want_emu and is_emu:
            self.update_status()
            return
        if not want_emu and is_sniff:
            self.update_status()
            return
        self._stop_worker()
        self.stop_event = threading.Event()
        if want_emu:
            self.worker = SlaveEmu(
                self.ser, self.q, self.stop_event, self._gap(),
                self.emu_keys, self.emu_data)
        else:
            self.worker = SnifferWorker(
                self.ser, self.q, self.stop_event, self._gap())
        self.worker.start()
        self.update_status()

    def _sync_slave_emu(self):
        """Совместимость: пересобрать worker анализа при смене галок."""
        self._ensure_analyze_worker()

    def _close_port(self):
        self._stop_worker()
        if self.ser is not None and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def open_settings(self):
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
        self.paused = True
        if self.mode_var.get() == 'analyze':
            self._ensure_analyze_worker()
        self.update_status()

    def open_master_settings(self):
        if self.mode_var.get() != 'master':
            messagebox.showinfo(
                "Режим", "Сначала выберите Режим → Мастер", parent=self.root)
            return
        self._stop_worker()
        self.paused = True
        dlg = MasterDialog(self.root, initial=self.master_cfg)
        result = dlg.wait()
        if result is not None:
            self.master_cfg = result
        self.update_status()

    # ---------- Анализ ----------
    def analyze_open_log(self):
        if self.mode_var.get() != 'analyze':
            return
        path = filedialog.askopenfilename(
            title="Лог для анализа",
            initialdir=str(default_logs_dir()),
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        self._analyze_source = path
        self.analyze_refresh()

    def analyze_refresh(self):
        if self.mode_var.get() != 'analyze':
            return
        try:
            if self._analyze_source:
                # явно открытый лог
                rows = analyze_log_file(self._analyze_source)
            elif self.history:
                # live: history надёжнее файла (и сразу виден)
                rows = analyze_frames(list(self.history))
            elif self.logger.path and Path(self.logger.path).is_file():
                # после clear экрана — из файла сессии
                rows = analyze_log_file(self.logger.path)
            else:
                rows = []
        except Exception as e:
            messagebox.showerror("Анализ", str(e), parent=self.root)
            return
        self._analyze_rows = rows
        self._fill_analyze_tree()
        self.update_status()

    def _fill_analyze_tree(self):
        self.tree.delete(*self.tree.get_children())
        for row in self._analyze_rows:
            key = row['key']
            iid = _key_str(key)
            emu = '☑' if key in self.emu_keys else '☐'
            edit = 'edit*' if key in self.emu_data else 'edit'
            period = ''
            if row.get('period_ms') is not None:
                period = f"{row['period_ms']:.0f}"
            self.tree.insert('', 'end', iid=iid, values=(
                emu, edit, row['slave'], row['type'], row['direction'],
                row['addr'], row['qty'],
                row['req_ok'], row['req_bad'],
                row['resp_ok'], row['resp_bad'], row['resp_miss'],
                period, row['status'],
            ))

    def _on_tree_click(self, event):
        if self.mode_var.get() != 'analyze':
            return
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        key = _parse_key(row_id)
        if col == '#1':  # emu
            self._toggle_emu_key(key, row_id)
        elif col == '#2':  # edit
            self._edit_emu_data(key, row_id)

    def _toggle_emu_key(self, key: tuple, row_id: str):
        if key in self.emu_keys:
            self.emu_keys.discard(key)
        else:
            if self.ser is None:
                messagebox.showinfo(
                    "Нет порта",
                    "Сначала откройте настройки порта (P).",
                    parent=self.root,
                )
                return
            self.emu_keys.add(key)
        vals = list(self.tree.item(row_id, 'values'))
        vals[0] = '☑' if key in self.emu_keys else '☐'
        self.tree.item(row_id, values=vals)
        self._ensure_analyze_worker()

    def _edit_emu_data(self, key: tuple, row_id: str):
        qty = key[4]
        dlg = EmuDataDialog(self.root, key, qty, self.emu_data.get(key))
        result = dlg.wait()
        if result is None:
            return
        # все нули — можно убрать кастомные данные
        if all(v == 0 for v in result):
            self.emu_data.pop(key, None)
        else:
            self.emu_data[key] = result
        vals = list(self.tree.item(row_id, 'values'))
        vals[1] = 'edit*' if key in self.emu_data else 'edit'
        self.tree.item(row_id, values=vals)

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
            _, raw, t_end, prev_end, *rest = msg
            tight = rest[0] if rest else False
            self._process_rx(raw, t_end, prev_end, tight)
        elif kind == 'tx':
            _, raw, t_end, prev_end = msg
            frame = Frame(raw, t_end, prev_end, 'TX')
            self._ingest(frame, always_show=True)
        elif kind == 'timeout':
            _, t_end, prev_end = msg
            frame = Frame(b'', t_end, prev_end, 'TIMEOUT')
            self._ingest(frame, always_show=True)
        elif kind == 'poll_done':
            self.worker = None
            self._master_once = False
            self.paused = True
            self.update_status()
        elif kind == 'error':
            print(f"Ошибка порта: {msg[1]}", file=sys.stderr, flush=True)
            # сбой записи emu — не закрывать порт (TCP window / timeout)
            if str(msg[1]).startswith('emu TX:'):
                return
            messagebox.showerror("Ошибка порта", msg[1], parent=self.root)
            self._close_port()
            self.paused = True
            self.worker = None
            self.update_status()

    def _process_rx(self, raw, t_end, prev_end, tight_gap: bool = False):
        frame, is_good, self.bad = classify(
            raw, t_end, prev_end, self.bad, tight_gap=tight_gap)
        if is_good:
            self.good += 1
        always = self.mode_var.get() == 'master'
        self._ingest(frame, always_show=always)

    def _ingest(self, frame: Frame, always_show: bool = False):
        self.logger.write_frame(frame)
        self.history.append(frame)
        if always_show or not self.paused:
            self._render_frame(frame)
        else:
            self.update_status()
        # таблица анализа — с задержкой, не на каждый кадр
        if self.mode_var.get() == 'analyze':
            self._schedule_analyze_refresh()

    def _schedule_analyze_refresh(self):
        if self._analyze_refresh_after is not None:
            return
        self._analyze_refresh_after = self.root.after(
            300, self._do_analyze_refresh)

    def _do_analyze_refresh(self):
        self._analyze_refresh_after = None
        self.analyze_refresh()

    # ---------- Рендер ----------
    def _refresh_display(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
        for frame in self.history:
            self._render_frame(frame, scroll=False)
        if self.autoscroll_var.get():
            self.text.see('end')
        self.update_status()

    def _render_frame(self, frame: Frame, scroll: bool = True):
        at_bottom = self.text.yview()[1] >= 0.999
        parts = frame.parts(
            self.raw_var.get(), self.inf_var.get(),
            self.raw_base_var.get(), self.exc_var.get(),
            compact=True,
        )
        self.text.configure(state='normal')
        for text, tag in parts:
            self.text.insert('end', text, tag)
        self.text.insert('end', '\n')
        self.text.configure(state='disabled')
        if scroll and (self.autoscroll_var.get() or at_bottom):
            self.text.see('end')

    # ---------- Действия ----------
    def toggle_pause(self):
        mode = self.mode_var.get()
        if mode == 'analyze':
            self._toggle_analyze()
            return
        if mode == 'master':
            self._toggle_master_poll()
            return

        self.paused = not self.paused
        if not self.paused:
            if self.ser is None:
                self.paused = True
                messagebox.showinfo(
                    "Нет порта",
                    "Сначала откройте настройки порта (P).",
                    parent=self.root,
                )
                self.update_status()
                return
            if self.worker is None:
                self._start_sniffer()
            self._refresh_display()
        else:
            self.update_status()

    def _toggle_analyze(self):
        """Space: старт/стоп прослушивания (и эмуляции) в режиме Анализ."""
        if not self.paused:
            self.paused = True
            self._ensure_analyze_worker()
            self.analyze_refresh()
            return
        if self.ser is None:
            messagebox.showinfo(
                "Нет порта",
                "Сначала откройте настройки порта (P).",
                parent=self.root,
            )
            self.update_status()
            return
        self.paused = False
        self._ensure_analyze_worker()
        self._refresh_display()

    def _toggle_master_poll(self):
        if self.ser is None:
            messagebox.showinfo(
                "Нет порта",
                "Сначала откройте настройки порта (P).",
                parent=self.root,
            )
            return
        if not self.paused and not self._master_once:
            self._stop_worker()
            self.paused = True
            self.update_status()
            return
        if self.worker is not None:
            self._stop_worker()
        self.paused = False
        self._start_master_poller(once=False)
        self._refresh_display()
        self.update_status()

    def master_send_once(self):
        if self.mode_var.get() != 'master':
            messagebox.showinfo(
                "Режим", "Сначала выберите Режим → Мастер", parent=self.root)
            return
        if self.ser is None:
            messagebox.showinfo(
                "Нет порта",
                "Сначала откройте настройки порта (P).",
                parent=self.root,
            )
            return
        if self.worker is not None:
            messagebox.showinfo(
                "Занято", "Дождитесь окончания текущего опроса", parent=self.root)
            return
        self._start_master_poller(once=True)
        self.update_status()

    def clear(self):
        if not messagebox.askyesno(
            "Очистка",
            "Очистить отображение?",
            parent=self.root,
        ):
            return
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
        self.update_status()

    def choose_log(self):
        initial = self.logger.path or str(default_logs_dir())
        path = filedialog.asksaveasfilename(
            title="Файл лога", defaultextension=".log",
            initialdir=str(Path(initial).parent),
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        try:
            self.logger.open(path)
            self.update_status()
        except Exception as e:
            print(f"Не удалось открыть лог: {e}", file=sys.stderr, flush=True)
            messagebox.showerror("Не удалось открыть лог", str(e), parent=self.root)
            try:
                self.logger.open_default_session()
            except Exception:
                pass
            self.update_status()

    def open_logs_dir(self):
        path = default_logs_dir()
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("Папка логов", str(e), parent=self.root)

    def update_status(self):
        mode = self.mode_var.get()
        if mode == 'master':
            if self._master_once and self.worker is not None:
                state = "SEND"
            elif not self.paused:
                state = "POLL"
            else:
                state = "IDLE"
        elif mode == 'analyze':
            if self.paused:
                state = "PAUSED"
            elif isinstance(self.worker, SlaveEmu):
                state = "EMU"
            else:
                state = "LIVE"
        else:
            state = "PAUSED" if self.paused else "LIVE"
        log_name = Path(self.logger.path).name if self.logger.path else 'off'
        c = self.cfg
        port = f"{c['port']}@{c['baud']}" if c else "no port"
        raw_base = self.raw_base_var.get()
        if mode == 'analyze':
            extra = (f"rows={len(self._analyze_rows)}  emu={len(self.emu_keys)}  "
                     f"[Space start/stop · F5 refresh · C clear · click emu]")
        elif mode == 'master':
            n_polls = len(self.master_cfg.get('polls') or [])
            extra = (f"polls={n_polls}  raw={raw_base}  "
                     f"[P port · M polls · S once · Space start/stop]")
        else:
            extra = (f"raw={raw_base}  "
                     f"[Space pause · C clear · P port · H hex/dec · R/I/A view]")
        self.status_lbl.configure(
            text=f"{state}  mode={mode}  {port}  good={self.good}  bad={self.bad}  "
                 f"frames={len(self.history)}  log={log_name}  {extra}"
        )

    def on_close(self):
        try:
            self._close_port()
            self.logger.close()
        finally:
            self.root.destroy()
