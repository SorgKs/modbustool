"""Генератор Modbus-кадров: таблица сценария, COM или TCP → modbustool."""
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from serial_io import TcpByteServer, compute_gap, list_com_ports, open_serial
from scenario import (
    DIRS, ROLES, TYPES, clone_step, default_step, frames_for_step,
    maybe_emit, step_label, validate_step,
)


COLS = ('#', 'role', 'slave', 'type', 'dir', 'addr', 'qty', 'values',
        'delay', 'resp_d', 'edit')
SOCKET_PRESET = 'socket://127.0.0.1:9502'


def _fmt_delay(lo, hi) -> str:
    lo, hi = int(lo or 0), int(hi or 0)
    if lo == 0 and hi == 0:
        return ''
    return f'{lo}-{hi}'


class StepDialog:
    """Добавить / править строку сценария. wait() → dict | None."""

    def __init__(self, parent, initial: dict | None = None):
        self.parent = parent
        self.result = None
        src = clone_step(initial) if initial else default_step()

        self.top = tk.Toplevel(parent)
        self.top.title("Шаг сценария")
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)

        self.role_var = tk.StringVar(value=src.get('role', 'req'))
        self.slave_var = tk.StringVar(value=str(src.get('slave', 1)))
        self.type_var = tk.StringVar(value=src.get('type', 'holding'))
        self.dir_var = tk.StringVar(value=src.get('direction', 'read'))
        self.addr_var = tk.StringVar(value=str(src.get('addr', 1)))
        self.qty_var = tk.StringVar(value=str(src.get('qty', 1)))
        vals = src.get('values') or []
        self.values_var = tk.StringVar(value=' '.join(str(v) for v in vals))
        self.delay_min_var = tk.StringVar(value=str(src.get('delay_ms_min', 0)))
        self.delay_max_var = tk.StringVar(value=str(src.get('delay_ms_max', 0)))
        self.resp_delay_min_var = tk.StringVar(
            value=str(src.get('resp_delay_ms_min', 0)))
        self.resp_delay_max_var = tk.StringVar(
            value=str(src.get('resp_delay_ms_max', 0)))

        row = 0
        for label, var, kind in (
            ('Role:', self.role_var, 'role'),
            ('Slave ID:', self.slave_var, 'entry'),
            ('Type:', self.type_var, 'type'),
            ('Direction:', self.dir_var, 'dir'),
            ('Address:', self.addr_var, 'entry'),
            ('Quantity:', self.qty_var, 'entry'),
            ('Values:', self.values_var, 'entry'),
        ):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky='w', pady=2)
            if kind == 'role':
                ttk.Combobox(frm, textvariable=var, values=list(ROLES), width=16,
                             state='readonly').grid(row=row, column=1, sticky='w', pady=2)
            elif kind == 'type':
                ttk.Combobox(frm, textvariable=var, values=list(TYPES), width=16,
                             state='readonly').grid(row=row, column=1, sticky='w', pady=2)
            elif kind == 'dir':
                ttk.Combobox(frm, textvariable=var, values=list(DIRS), width=16,
                             state='readonly').grid(row=row, column=1, sticky='w', pady=2)
            else:
                ttk.Entry(frm, textvariable=var, width=36).grid(
                    row=row, column=1, sticky='ew', pady=2)
            row += 1

        ttk.Label(frm, text="Delay ms:").grid(row=row, column=0, sticky='w', pady=2)
        drow = ttk.Frame(frm)
        drow.grid(row=row, column=1, sticky='w', pady=2)
        ttk.Entry(drow, textvariable=self.delay_min_var, width=8).pack(side='left')
        ttk.Label(drow, text="–").pack(side='left', padx=2)
        ttk.Entry(drow, textvariable=self.delay_max_var, width=8).pack(side='left')
        row += 1

        ttk.Label(frm, text="Resp delay ms:").grid(row=row, column=0, sticky='w', pady=2)
        rrow = ttk.Frame(frm)
        rrow.grid(row=row, column=1, sticky='w', pady=2)
        ttk.Entry(rrow, textvariable=self.resp_delay_min_var, width=8).pack(side='left')
        ttk.Label(rrow, text="–").pack(side='left', padx=2)
        ttk.Entry(rrow, textvariable=self.resp_delay_max_var, width=8).pack(side='left')
        ttk.Label(rrow, text="(pair)").pack(side='left', padx=(6, 0))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(12, 0), sticky='e')
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side='right')

        self.top.bind('<Return>', lambda e: self._ok())
        self.top.bind('<Escape>', lambda e: self._cancel())
        self.top.grab_set()

    def wait(self):
        self.parent.wait_window(self.top)
        return self.result

    def _ok(self):
        raw = self.values_var.get().strip()
        try:
            values = [int(x, 0) for x in raw.split()] if raw else []
            step = {
                'role': self.role_var.get(),
                'slave': int(self.slave_var.get(), 0),
                'type': self.type_var.get(),
                'direction': self.dir_var.get(),
                'addr': int(self.addr_var.get(), 0),
                'qty': int(self.qty_var.get(), 0),
                'values': values,
                'delay_ms_min': int(self.delay_min_var.get()),
                'delay_ms_max': int(self.delay_max_var.get()),
                'resp_delay_ms_min': int(self.resp_delay_min_var.get()),
                'resp_delay_ms_max': int(self.resp_delay_max_var.get()),
            }
            validate_step(step)
            frames_for_step(step)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e), parent=self.top)
            return
        self.result = step
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()


class MbGenApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("mbgen — генератор Modbus")
        self.steps: list[dict] = []
        self.transport = None  # Serial | TcpByteServer
        self._run_thread = None
        self._stop = threading.Event()
        self._log_q: queue.Queue = queue.Queue()
        self._gap = compute_gap(9600, 'N', 1)  # мин. пауза RTU между кадрами

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_log)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='both', expand=True)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(2, weight=1)

        # --- канал ---
        ch = ttk.LabelFrame(top, text="Канал", padding=6)
        ch.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        self.mode_var = tk.StringVar(value='tcp')
        ttk.Radiobutton(ch, text="TCP server", variable=self.mode_var,
                        value='tcp', command=self._on_mode).pack(side='left')
        ttk.Radiobutton(ch, text="COM", variable=self.mode_var,
                        value='com', command=self._on_mode).pack(side='left', padx=(8, 0))

        self.tcp_host_var = tk.StringVar(value='127.0.0.1')
        self.tcp_port_var = tk.StringVar(value='9502')
        ttk.Label(ch, text="host:").pack(side='left', padx=(12, 2))
        ttk.Entry(ch, textvariable=self.tcp_host_var, width=12).pack(side='left')
        ttk.Label(ch, text="port:").pack(side='left', padx=(6, 2))
        ttk.Entry(ch, textvariable=self.tcp_port_var, width=6).pack(side='left')

        devices, labels = list_com_ports()
        self._com_devices = devices
        self.com_var = tk.StringVar()
        self.com_cb = ttk.Combobox(ch, textvariable=self.com_var, values=labels, width=28)
        self.com_cb.pack(side='left', padx=(12, 0))
        if labels and devices[0]:
            self.com_cb.current(0)

        self.baud_var = tk.StringVar(value='9600')
        ttk.Label(ch, text="baud:").pack(side='left', padx=(8, 2))
        ttk.Combobox(ch, textvariable=self.baud_var,
                     values=['9600', '19200', '38400', '115200'],
                     width=8, state='readonly').pack(side='left')

        ttk.Button(ch, text="Open", command=self._open_channel).pack(side='left', padx=(12, 2))
        ttk.Button(ch, text="Close", command=self._close_channel).pack(side='left')
        self.chan_status = ttk.Label(ch, text="закрыт")
        self.chan_status.pack(side='left', padx=(8, 0))

        # --- параметры сбоев / задержек ---
        params = ttk.LabelFrame(top, text="Параметры", padding=6)
        params.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.delay_min_var = tk.StringVar(value='0')
        self.delay_max_var = tk.StringVar(value='0')
        self.resp_delay_min_var = tk.StringVar(value='0')
        self.resp_delay_max_var = tk.StringVar(value='0')
        self.drop_var = tk.StringVar(value='0')
        self.corrupt_var = tk.StringVar(value='0')

        def _pair(parent, label, vmin, vmax):
            f = ttk.Frame(parent)
            ttk.Label(f, text=label).pack(side='left')
            ttk.Entry(f, textvariable=vmin, width=6).pack(side='left', padx=2)
            ttk.Label(f, text="–").pack(side='left')
            ttk.Entry(f, textvariable=vmax, width=6).pack(side='left', padx=2)
            return f

        _pair(params, "delay ms (all):", self.delay_min_var, self.delay_max_var).pack(
            side='left', padx=(0, 10))
        _pair(params, "resp delay (all):", self.resp_delay_min_var,
              self.resp_delay_max_var).pack(side='left', padx=(0, 10))
        ttk.Label(params, text="drop %:").pack(side='left')
        ttk.Entry(params, textvariable=self.drop_var, width=5).pack(side='left', padx=(2, 10))
        ttk.Label(params, text="corrupt %:").pack(side='left')
        ttk.Entry(params, textvariable=self.corrupt_var, width=5).pack(side='left', padx=2)

        ttk.Button(params, text="Send once", command=self._send_once).pack(
            side='right', padx=2)
        self.cycle_btn = ttk.Button(params, text="Start", command=self._toggle_cycle)
        self.cycle_btn.pack(side='right', padx=2)

        # --- таблица ---
        mid = ttk.Frame(top)
        mid.grid(row=2, column=0, sticky='nsew')
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(mid, columns=COLS, show='headings', height=12,
                                 selectmode='browse')
        widths = {'#': 36, 'role': 50, 'slave': 50, 'type': 70, 'dir': 50,
                  'addr': 56, 'qty': 44, 'values': 120, 'delay': 64,
                  'resp_d': 64, 'edit': 44}
        for c in COLS:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c, 60), anchor='w')
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=0, column=1, sticky='ns')
        self.tree.bind('<Double-1>', self._on_tree_dbl)

        row_btns = ttk.Frame(top)
        row_btns.grid(row=3, column=0, sticky='w', pady=4)
        ttk.Button(row_btns, text="Add", command=self._add).pack(side='left', padx=(0, 4))
        ttk.Button(row_btns, text="Edit", command=self._edit).pack(side='left', padx=(0, 4))
        ttk.Button(row_btns, text="Remove", command=self._remove).pack(side='left')

        # --- лог ---
        log_frm = ttk.LabelFrame(top, text="Лог", padding=4)
        log_frm.grid(row=4, column=0, sticky='nsew', pady=(4, 0))
        top.rowconfigure(4, weight=1)
        self.log = tk.Text(log_frm, height=10, wrap='none', state='disabled')
        lsb = ttk.Scrollbar(log_frm, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side='left', fill='both', expand=True)
        lsb.pack(side='right', fill='y')

        self._on_mode()

    def _on_mode(self):
        tcp = self.mode_var.get() == 'tcp'
        state_com = 'disabled' if tcp else 'readonly'
        self.com_cb.configure(state=state_com)

    def _selected_idx(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(self.tree.index(sel[0]))
        except (ValueError, tk.TclError):
            return None

    def _refresh_tree(self, select: int | None = None):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.steps):
            vals = s.get('values') or []
            vals_s = ' '.join(str(v) for v in vals) if vals else ''
            self.tree.insert('', 'end', iid=str(i), values=(
                i + 1, s['role'], s['slave'], s['type'], s['direction'],
                s['addr'], s['qty'], vals_s,
                _fmt_delay(s.get('delay_ms_min'), s.get('delay_ms_max')),
                _fmt_delay(s.get('resp_delay_ms_min'), s.get('resp_delay_ms_max'))
                if s.get('role') == 'pair' else '',
                'edit',
            ))
        if select is not None and 0 <= select < len(self.steps):
            self.tree.selection_set(str(select))
            self.tree.focus(str(select))

    def _add(self):
        dlg = StepDialog(self.root)
        step = dlg.wait()
        if step is None:
            return
        self.steps.append(step)
        self._refresh_tree(select=len(self.steps) - 1)

    def _edit(self):
        idx = self._selected_idx()
        if idx is None:
            return
        dlg = StepDialog(self.root, self.steps[idx])
        step = dlg.wait()
        if step is None:
            return
        self.steps[idx] = step
        self._refresh_tree(select=idx)

    def _on_tree_dbl(self, event):
        col = self.tree.identify_column(event.x)
        # колонка edit — последняя (#9)
        if col == f'#{len(COLS)}':
            self._edit()

    def _remove(self):
        idx = self._selected_idx()
        if idx is None:
            return
        del self.steps[idx]
        self._refresh_tree(select=min(idx, len(self.steps) - 1) if self.steps else None)

    def _append_log(self, line: str):
        self.log.configure(state='normal')
        self.log.insert('end', line + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _drain_log(self):
        try:
            while True:
                line = self._log_q.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    def _parse_params(self):
        dmin = int(self.delay_min_var.get())
        dmax = int(self.delay_max_var.get())
        rmin = int(self.resp_delay_min_var.get())
        rmax = int(self.resp_delay_max_var.get())
        if dmin < 0 or dmax < 0 or rmin < 0 or rmax < 0:
            raise ValueError('delay >= 0')
        if dmin > dmax or rmin > rmax:
            raise ValueError('delay min > max')
        drop = float(self.drop_var.get())
        corrupt = float(self.corrupt_var.get())
        if not (0 <= drop <= 100 and 0 <= corrupt <= 100):
            raise ValueError('drop/corrupt 0..100')
        return dmin, dmax, rmin, rmax, drop, corrupt

    def _open_channel(self):
        self._close_channel()
        try:
            baud = int(self.baud_var.get())
            # TX: мин. 3.5 символа (без искусственного пола 10 мс)
            self._gap = compute_gap(baud, 'N', 1)
            if self.mode_var.get() == 'tcp':
                host = self.tcp_host_var.get().strip() or '127.0.0.1'
                port = int(self.tcp_port_var.get())
                srv = TcpByteServer(host, port)
                srv.start()
                self.transport = srv
                self.chan_status.configure(
                    text=f"TCP {host}:{port} (ждём {SOCKET_PRESET})")
            else:
                idx = self.com_cb.current()
                if idx < 0 or not self._com_devices[idx]:
                    raise ValueError('не выбран COM')
                self.transport = open_serial(
                    self._com_devices[idx], baud, 'N', 1)
                self.chan_status.configure(
                    text=f"COM {self._com_devices[idx]} @{baud}")
        except Exception as e:
            self.transport = None
            messagebox.showerror("Канал", str(e), parent=self.root)
            self.chan_status.configure(text="ошибка")

    def _close_channel(self):
        self._stop.set()
        if self._run_thread and self._run_thread.is_alive():
            self._run_thread.join(timeout=2.0)
        self._run_thread = None
        self._stop = threading.Event()
        self.cycle_btn.configure(text="Start")
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:
                pass
            self.transport = None
        self.chan_status.configure(text="закрыт")

    def _send_once(self):
        if not self.steps:
            messagebox.showinfo("Сценарий", "Таблица пуста", parent=self.root)
            return
        if self.transport is None:
            messagebox.showerror("Канал", "Сначала Open", parent=self.root)
            return
        if self._run_thread and self._run_thread.is_alive():
            messagebox.showinfo("", "Уже идёт цикл", parent=self.root)
            return
        try:
            params = self._parse_params()
        except Exception as e:
            messagebox.showerror("Параметры", str(e), parent=self.root)
            return
        self._stop = threading.Event()
        self._run_thread = threading.Thread(
            target=self._run_loop, args=(params, True), daemon=True)
        self._run_thread.start()

    def _toggle_cycle(self):
        if self._run_thread and self._run_thread.is_alive():
            self._stop.set()
            self.cycle_btn.configure(text="Start")
            return
        if not self.steps:
            messagebox.showinfo("Сценарий", "Таблица пуста", parent=self.root)
            return
        if self.transport is None:
            messagebox.showerror("Канал", "Сначала Open", parent=self.root)
            return
        try:
            params = self._parse_params()
        except Exception as e:
            messagebox.showerror("Параметры", str(e), parent=self.root)
            return
        self._stop = threading.Event()
        self.cycle_btn.configure(text="Stop")
        self._run_thread = threading.Thread(
            target=self._run_loop, args=(params, False), daemon=True)
        self._run_thread.start()

    def _sleep_ms(self, lo: int, hi: int):
        """Пауза: max(3.5 символа RTU, random из вилки)."""
        gap_ms = max(1, int(self._gap * 1000 + 0.999))  # ceil
        ms = random.randint(lo, hi) if (hi > 0 or lo > 0) else 0
        ms = max(ms, gap_ms)
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            if self._stop.is_set():
                return
            time.sleep(min(0.02, max(0.0, end - time.monotonic())))

    def _emit_one(self, frame: bytes, drop: float, corrupt: float) -> bool:
        """TX одного кадра. True если канал жив."""
        out, tag = maybe_emit(frame, drop, corrupt)
        if tag == 'DROP':
            self._log_q.put(f"DROP  {frame.hex(' ')}")
            return True
        if self._stop.is_set():
            return False
        try:
            if isinstance(self.transport, TcpByteServer):
                self.transport.write(
                    out,
                    on_block=lambda: self._log_q.put(
                        'BUF    переполнен, жду...'),
                    should_abort=lambda: self._stop.is_set(),
                )
            else:
                self.transport.write(out)
            self.transport.flush()
        except Exception as e:
            self._log_q.put(f"ERR   {e}")
            return False
        hx = out.hex(' ')
        self._log_q.put(f"{tag:7} {hx}")
        return True

    def _run_loop(self, params, once: bool):
        dmin, dmax, rmin, rmax, drop, corrupt = params
        try:
            while not self._stop.is_set():
                for step in list(self.steps):
                    if self._stop.is_set():
                        break
                    try:
                        frames = frames_for_step(step)
                    except Exception as e:
                        self._log_q.put(f"SKIP  {step_label(step)}: {e}")
                        continue
                    role = step['role']
                    s_dmin = int(step.get('delay_ms_min', 0))
                    s_dmax = int(step.get('delay_ms_max', 0))
                    s_rmin = int(step.get('resp_delay_ms_min', 0))
                    s_rmax = int(step.get('resp_delay_ms_max', 0))
                    # индивидуальная ненулевая вилка заменяет общую
                    use_dmin, use_dmax = (
                        (s_dmin, s_dmax) if (s_dmin or s_dmax) else (dmin, dmax))
                    use_rmin, use_rmax = (
                        (s_rmin, s_rmax) if (s_rmin or s_rmax) else (rmin, rmax))
                    if role == 'pair':
                        self._sleep_ms(use_dmin, use_dmax)
                        if self._stop.is_set():
                            break
                        if not self._emit_one(frames[0], drop, corrupt):
                            return
                        self._sleep_ms(use_rmin, use_rmax)
                        if self._stop.is_set():
                            break
                        if not self._emit_one(frames[1], drop, corrupt):
                            return
                    else:
                        self._sleep_ms(use_dmin, use_dmax)
                        if self._stop.is_set():
                            break
                        if not self._emit_one(frames[0], drop, corrupt):
                            return
                if once:
                    break
        finally:
            self.root.after(0, lambda: self.cycle_btn.configure(text="Start"))

    def _on_close(self):
        self._close_channel()
        self.root.destroy()


def main():
    root = tk.Tk()
    MbGenApp(root)
    root.minsize(720, 480)
    root.mainloop()


if __name__ == '__main__':
    main()
