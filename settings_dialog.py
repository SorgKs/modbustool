"""Модальное окно настроек порта (рисуется на root, без Toplevel)."""
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import serial.tools.list_ports


BAUDS = ['1200', '2400', '4800', '9600', '19200', '38400', '57600', '115200']
PARITIES = ['None', 'Even', 'Odd']
STOPS = ['1', '2']
PARITY_MAP = {'None': 'N', 'Even': 'E', 'Odd': 'O'}


class SettingsDialog:
    """Настройки на переданном Tk/Frame. Ждать через wait()."""

    def __init__(self, master: tk.Tk):
        self.master = master
        self.result = None
        self._done = tk.BooleanVar(master=master, value=False)

        master.title("Modbus RTU Sniffer — Settings")
        master.resizable(False, False)
        master.protocol("WM_DELETE_WINDOW", self._cancel)

        self._port_devices, labels = self._list_ports()

        frm = ttk.Frame(master, padding=12)
        frm.grid(row=0, column=0, sticky='nsew')
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Port:").grid(row=0, column=0, sticky='w', pady=3)
        self.port_var = tk.StringVar(value=labels[0] if labels else '')
        self.port_cb = ttk.Combobox(frm, textvariable=self.port_var,
                                    values=labels, width=50, state='readonly')
        self.port_cb.grid(row=0, column=1, sticky='ew', pady=3)

        ttk.Label(frm, text="Baud rate:").grid(row=1, column=0, sticky='w', pady=3)
        self.baud_var = tk.StringVar(value='9600')
        ttk.Combobox(frm, textvariable=self.baud_var, values=BAUDS,
                     width=14, state='readonly').grid(row=1, column=1, sticky='w', pady=3)

        ttk.Label(frm, text="Parity:").grid(row=2, column=0, sticky='w', pady=3)
        self.parity_var = tk.StringVar(value='None')
        ttk.Combobox(frm, textvariable=self.parity_var, values=PARITIES,
                     width=14, state='readonly').grid(row=2, column=1, sticky='w', pady=3)

        ttk.Label(frm, text="Stop bits:").grid(row=3, column=0, sticky='w', pady=3)
        self.stop_var = tk.StringVar(value='1')
        ttk.Combobox(frm, textvariable=self.stop_var, values=STOPS,
                     width=14, state='readonly').grid(row=3, column=1, sticky='w', pady=3)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, pady=(14, 0), sticky='e')
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text="Start", command=self._start).pack(side='right')

        master.bind('<Return>', lambda e: self._start())
        master.bind('<Escape>', lambda e: self._cancel())

        # центрирование после расчёта размера
        master.update_idletasks()
        w, h = master.winfo_reqwidth(), master.winfo_reqheight()
        x = master.winfo_screenwidth() // 2 - w // 2
        y = master.winfo_screenheight() // 2 - h // 2
        master.geometry(f"{w}x{h}+{x}+{y}")

    def wait(self):
        """Блокирует до Start/Cancel, обрабатывая события Tk."""
        self.master.wait_variable(self._done)

    def clear(self):
        """Убирает виджеты настроек с root перед главным окном."""
        for w in self.master.winfo_children():
            w.destroy()
        self.master.unbind('<Return>')
        self.master.unbind('<Escape>')
        self.master.resizable(True, True)

    @staticmethod
    def _list_ports():
        devices, labels = [], []
        for p in serial.tools.list_ports.comports():
            desc = p.description if p.description and p.description != 'n/a' else ''
            devices.append(p.device)
            labels.append(f"{p.device}  —  {desc}" if desc else p.device)
        if not labels:
            devices = ['']
            labels = ['(порты не найдены)']
        return devices, labels

    def _finish(self):
        self._done.set(True)

    def _start(self):
        idx = self.port_cb.current()
        if idx < 0 or not self._port_devices[idx]:
            print("Не выбран COM-порт", file=sys.stderr, flush=True)
            messagebox.showerror("Ошибка", "Не выбран COM-порт", parent=self.master)
            return
        self.result = {
            'port': self._port_devices[idx],
            'baud': int(self.baud_var.get()),
            'parity': PARITY_MAP[self.parity_var.get()],
            'stop': int(self.stop_var.get()),
        }
        self._finish()

    def _cancel(self):
        self.result = None
        self._finish()
