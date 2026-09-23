"""Модальное окно настроек порта (Toplevel)."""
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import serial.tools.list_ports


BAUDS = ['1200', '2400', '4800', '9600', '19200', '38400', '57600', '115200']
PARITIES = ['None', 'Even', 'Odd']
STOPS = ['1', '2']
PARITY_MAP = {'None': 'N', 'Even': 'E', 'Odd': 'O'}
PARITY_LABEL = {'N': 'None', 'E': 'Even', 'O': 'Odd'}


class SettingsDialog:
    """Отдельное окно настроек. Ждать через wait() → dict | None."""

    def __init__(self, parent: tk.Tk, initial: dict | None = None):
        self.parent = parent
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("Настройки порта")
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        self._port_devices, labels = self._list_ports()
        # подставить текущие значения при повторном открытии
        init_port = (initial or {}).get('port', '')
        init_baud = str((initial or {}).get('baud', 9600))
        init_parity = PARITY_LABEL.get((initial or {}).get('parity', 'N'), 'None')
        init_stop = str((initial or {}).get('stop', 1))

        frm = ttk.Frame(self.top, padding=12)
        frm.grid(row=0, column=0, sticky='nsew')
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Port:").grid(row=0, column=0, sticky='w', pady=3)
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(frm, textvariable=self.port_var,
                                    values=labels, width=50, state='readonly')
        self.port_cb.grid(row=0, column=1, sticky='ew', pady=3)
        self._select_port(init_port, labels)

        ttk.Label(frm, text="Baud rate:").grid(row=1, column=0, sticky='w', pady=3)
        self.baud_var = tk.StringVar(value=init_baud if init_baud in BAUDS else '9600')
        ttk.Combobox(frm, textvariable=self.baud_var, values=BAUDS,
                     width=14, state='readonly').grid(row=1, column=1, sticky='w', pady=3)

        ttk.Label(frm, text="Parity:").grid(row=2, column=0, sticky='w', pady=3)
        self.parity_var = tk.StringVar(value=init_parity)
        ttk.Combobox(frm, textvariable=self.parity_var, values=PARITIES,
                     width=14, state='readonly').grid(row=2, column=1, sticky='w', pady=3)

        ttk.Label(frm, text="Stop bits:").grid(row=3, column=0, sticky='w', pady=3)
        self.stop_var = tk.StringVar(value=init_stop if init_stop in STOPS else '1')
        ttk.Combobox(frm, textvariable=self.stop_var, values=STOPS,
                     width=14, state='readonly').grid(row=3, column=1, sticky='w', pady=3)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, pady=(14, 0), sticky='e')
        ok_text = "OK" if initial else "Start"
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text=ok_text, command=self._ok).pack(side='right')

        self.top.bind('<Return>', lambda e: self._ok())
        self.top.bind('<Escape>', lambda e: self._cancel())

        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = self.top.winfo_screenwidth() // 2 - w // 2
        y = self.top.winfo_screenheight() // 2 - h // 2
        self.top.geometry(f"{w}x{h}+{x}+{y}")

        self.top.grab_set()
        self.top.focus_set()

    def wait(self):
        """Блокирует до OK/Cancel, возвращает result."""
        self.parent.wait_window(self.top)
        return self.result

    def _select_port(self, device: str, labels: list):
        """Выбрать порт по device или первый в списке."""
        if device and device in self._port_devices:
            idx = self._port_devices.index(device)
            self.port_cb.current(idx)
            return
        if labels and self._port_devices[0]:
            self.port_cb.current(0)
        else:
            self.port_var.set(labels[0] if labels else '')

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

    def _ok(self):
        idx = self.port_cb.current()
        if idx < 0 or not self._port_devices[idx]:
            print("Не выбран COM-порт", file=sys.stderr, flush=True)
            messagebox.showerror("Ошибка", "Не выбран COM-порт", parent=self.top)
            return
        self.result = {
            'port': self._port_devices[idx],
            'baud': int(self.baud_var.get()),
            'parity': PARITY_MAP[self.parity_var.get()],
            'stop': int(self.stop_var.get()),
        }
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()
