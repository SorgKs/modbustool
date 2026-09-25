"""Диалог ввода данных ответа эмулятора."""
import tkinter as tk
from tkinter import ttk, messagebox


class EmuDataDialog:
    """Ввод values через пробел. wait() → list[int] | None (Cancel)."""

    def __init__(self, parent, key: tuple, qty: int, initial: list[int] | None):
        self.parent = parent
        self.result = None
        self.qty = qty
        slave, typ, direction, addr, _q = key

        self.top = tk.Toplevel(parent)
        self.top.title("Данные ответа")
        self.top.resizable(True, False)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        frm.columnconfigure(0, weight=1)

        ttk.Label(
            frm,
            text=f"id={slave}  {typ} {direction}  addr={addr}  qty={qty}",
        ).grid(row=0, column=0, sticky='w', pady=(0, 6))
        hint = " 0/1" if typ in ('coils', 'discrete') else " 0..65535"
        ttk.Label(frm, text=f"Значения через пробел ({qty} шт.):{hint}").grid(
            row=1, column=0, sticky='w')

        init = ' '.join(str(v) for v in (initial or [0] * qty))
        self.var = tk.StringVar(value=init)
        ent = ttk.Entry(frm, textvariable=self.var, width=56)
        ent.grid(row=2, column=0, sticky='ew', pady=6)
        ent.focus_set()

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, sticky='e')
        ttk.Button(btns, text="Zeros", command=self._zeros).pack(side='left', padx=(0, 8))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side='right')

        self.top.bind('<Return>', lambda e: self._ok())
        self.top.bind('<Escape>', lambda e: self._cancel())
        self.top.grab_set()

    def wait(self):
        self.parent.wait_window(self.top)
        return self.result

    def _zeros(self):
        self.var.set(' '.join(['0'] * self.qty))

    def _ok(self):
        raw = self.var.get().strip()
        if not raw:
            vals = [0] * self.qty
        else:
            try:
                vals = [int(x, 0) for x in raw.split()]
            except ValueError:
                messagebox.showerror("Ошибка", "Нужны целые числа", parent=self.top)
                return
        if len(vals) != self.qty:
            messagebox.showerror(
                "Ошибка", f"Нужно ровно {self.qty} значений, сейчас {len(vals)}",
                parent=self.top,
            )
            return
        self.result = vals
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()
