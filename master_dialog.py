"""Диалог настройки списка опросов мастера."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox

from master import default_master_cfg, default_poll


TYPES = ['holding', 'input', 'coils']
DIRS = ['read', 'write']


def _poll_label(p: dict) -> str:
    vals = p.get('values') or []
    extra = f" vals={vals}" if p.get('direction') == 'write' and vals else ''
    return (f"id={p['slave']} {p['type']} {p['direction']} "
            f"addr={p['addr']} qty={p['qty']}{extra}")


class MasterDialog:
    """Список опросов. wait() → cfg dict | None."""

    def __init__(self, parent: tk.Tk, initial: dict | None = None):
        self.parent = parent
        self.result = None
        src = copy.deepcopy(initial) if initial else default_master_cfg()
        self._polls = list(src.get('polls') or [default_poll()])
        if not self._polls:
            self._polls = [default_poll()]

        self.top = tk.Toplevel(parent)
        self.top.title("Настройка опроса")
        self.top.resizable(True, True)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=12)
        frm.grid(row=0, column=0, sticky='nsew')
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(0, weight=1)

        # --- список ---
        left = ttk.Frame(frm)
        left.grid(row=0, column=0, columnspan=2, sticky='nsew', pady=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(left, height=8, width=64, exportselection=False)
        self.listbox.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(left, orient='vertical', command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>', self._on_select)

        btns_list = ttk.Frame(frm)
        btns_list.grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))
        ttk.Button(btns_list, text="Add", command=self._add).pack(side='left', padx=(0, 4))
        ttk.Button(btns_list, text="Remove", command=self._remove).pack(side='left')

        # --- поля выбранного ---
        self.slave_var = tk.StringVar(value='1')
        self.type_var = tk.StringVar(value='holding')
        self.dir_var = tk.StringVar(value='read')
        self.addr_var = tk.StringVar(value='1')
        self.qty_var = tk.StringVar(value='1')
        self.values_var = tk.StringVar(value='')

        row = 2
        for label, var, widget in (
            ('Slave ID:', self.slave_var, 'entry'),
            ('Type:', self.type_var, 'type'),
            ('Direction:', self.dir_var, 'dir'),
            ('Address:', self.addr_var, 'entry'),
            ('Quantity:', self.qty_var, 'entry'),
            ('Values:', self.values_var, 'entry'),
        ):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky='w', pady=2)
            if widget == 'type':
                ttk.Combobox(frm, textvariable=var, values=TYPES, width=16,
                             state='readonly').grid(row=row, column=1, sticky='w', pady=2)
            elif widget == 'dir':
                ttk.Combobox(frm, textvariable=var, values=DIRS, width=16,
                             state='readonly').grid(row=row, column=1, sticky='w', pady=2)
            else:
                ttk.Entry(frm, textvariable=var, width=40).grid(
                    row=row, column=1, sticky='ew', pady=2)
            row += 1

        self.type_var.trace_add('write', lambda *_: self._apply_fields())
        self.dir_var.trace_add('write', lambda *_: self._apply_fields())
        for v in (self.slave_var, self.addr_var, self.qty_var, self.values_var):
            v.trace_add('write', lambda *_: self._apply_fields())

        ttk.Label(frm, text="Timeout ms:").grid(row=row, column=0, sticky='w', pady=2)
        self.timeout_var = tk.StringVar(value=str(src.get('timeout_ms', 1000)))
        ttk.Entry(frm, textvariable=self.timeout_var, width=12).grid(
            row=row, column=1, sticky='w', pady=2)
        row += 1
        ttk.Label(frm, text="Interval ms:").grid(row=row, column=0, sticky='w', pady=2)
        self.interval_var = tk.StringVar(value=str(src.get('interval_ms', 1000)))
        ttk.Entry(frm, textvariable=self.interval_var, width=12).grid(
            row=row, column=1, sticky='w', pady=2)
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(12, 0), sticky='e')
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side='right')

        self._refresh_list(select=0)
        self._loading = False

        self.top.bind('<Return>', lambda e: self._ok())
        self.top.bind('<Escape>', lambda e: self._cancel())
        self.top.update_idletasks()
        w, h = max(self.top.winfo_reqwidth(), 480), max(self.top.winfo_reqheight(), 360)
        x = self.top.winfo_screenwidth() // 2 - w // 2
        y = self.top.winfo_screenheight() // 2 - h // 2
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        self.top.grab_set()
        self.top.focus_set()

    def wait(self):
        self.parent.wait_window(self.top)
        return self.result

    def _selected_index(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _refresh_list(self, select=None):
        self.listbox.delete(0, 'end')
        for p in self._polls:
            self.listbox.insert('end', _poll_label(p))
        if select is not None and self._polls:
            idx = max(0, min(select, len(self._polls) - 1))
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self._load_fields(idx)

    def _on_select(self, _event=None):
        idx = self._selected_index()
        if idx is not None:
            self._load_fields(idx)

    def _load_fields(self, idx: int):
        self._loading = True
        p = self._polls[idx]
        self.slave_var.set(str(p['slave']))
        self.type_var.set(p['type'])
        self.dir_var.set(p['direction'])
        self.addr_var.set(str(p['addr']))
        self.qty_var.set(str(p['qty']))
        self.values_var.set(' '.join(str(v) for v in (p.get('values') or [])))
        self._loading = False

    def _apply_fields(self):
        if getattr(self, '_loading', True):
            return
        idx = self._selected_index()
        if idx is None:
            return
        try:
            slave = int(self.slave_var.get())
            addr = int(self.addr_var.get())
            qty = int(self.qty_var.get())
        except ValueError:
            return
        vals_raw = self.values_var.get().strip()
        values = [int(x) for x in vals_raw.split()] if vals_raw else []
        self._polls[idx] = {
            'slave': slave,
            'type': self.type_var.get(),
            'direction': self.dir_var.get(),
            'addr': addr,
            'qty': qty,
            'values': values,
        }
        # обновить подпись без сброса selection
        self.listbox.delete(idx)
        self.listbox.insert(idx, _poll_label(self._polls[idx]))
        self.listbox.selection_set(idx)

    def _add(self):
        self._apply_fields()
        self._polls.append(default_poll())
        self._refresh_list(select=len(self._polls) - 1)

    def _remove(self):
        idx = self._selected_index()
        if idx is None or len(self._polls) <= 1:
            messagebox.showinfo("Список", "Нужен хотя бы один опрос", parent=self.top)
            return
        del self._polls[idx]
        self._refresh_list(select=min(idx, len(self._polls) - 1))

    def _ok(self):
        self._apply_fields()
        if not self._polls:
            messagebox.showerror("Ошибка", "Список опросов пуст", parent=self.top)
            return
        try:
            timeout_ms = int(self.timeout_var.get())
            interval_ms = int(self.interval_var.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Timeout/Interval — целые мс", parent=self.top)
            return
        if timeout_ms < 1 or interval_ms < 0:
            messagebox.showerror("Ошибка", "Некорректный timeout/interval", parent=self.top)
            return

        polls = []
        for i, p in enumerate(self._polls):
            if p['type'] == 'input' and p['direction'] == 'write':
                messagebox.showerror(
                    "Ошибка", f"Опрос #{i + 1}: input только read", parent=self.top)
                return
            if not (1 <= p['slave'] <= 247):
                messagebox.showerror(
                    "Ошибка", f"Опрос #{i + 1}: slave 1..247", parent=self.top)
                return
            if p['qty'] < 1:
                messagebox.showerror(
                    "Ошибка", f"Опрос #{i + 1}: quantity >= 1", parent=self.top)
                return
            if p['direction'] == 'write' and len(p.get('values') or []) != p['qty']:
                messagebox.showerror(
                    "Ошибка",
                    f"Опрос #{i + 1}: нужно {p['qty']} value(s)",
                    parent=self.top,
                )
                return
            polls.append(dict(p))

        self.result = {
            'timeout_ms': timeout_ms,
            'interval_ms': interval_ms,
            'polls': polls,
        }
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()
