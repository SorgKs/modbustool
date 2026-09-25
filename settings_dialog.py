"""Модальное окно настроек порта (Toplevel)."""
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from serial_io import is_url_port, list_com_ports


BAUDS = ['1200', '2400', '4800', '9600', '19200', '38400', '57600', '115200']
PARITIES = ['None', 'Even', 'Odd']
STOPS = ['1', '2']
PARITY_MAP = {'None': 'N', 'Even': 'E', 'Odd': 'O'}
PARITY_LABEL = {'N': 'None', 'E': 'Even', 'O': 'Odd'}
SOCKET_PRESET = 'socket://127.0.0.1:9502'


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

        self._port_devices, labels = list_com_ports()
        # editable: COM из списка или URL
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
                                    values=labels, width=50)
        self.port_cb.grid(row=0, column=1, sticky='ew', pady=3)
        self._com_saved = ''  # последний COM при переключении на socket

        self.socket_var = tk.BooleanVar(value=bool(init_port and is_url_port(init_port)))
        ttk.Checkbutton(
            frm, text="socket://", variable=self.socket_var,
            command=self._on_socket_toggle,
        ).grid(row=0, column=2, padx=(6, 0), pady=3)

        if self.socket_var.get():
            self._com_saved = ''
            self.port_cb.configure(state='disabled')
            self.port_var.set(init_port if is_url_port(init_port) else SOCKET_PRESET)
        else:
            self._select_port(init_port, labels)
            self.port_cb.configure(state='readonly')

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
        btns.grid(row=4, column=0, columnspan=3, pady=(14, 0), sticky='e')
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side='right', padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side='right')

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

    def _on_socket_toggle(self):
        if self.socket_var.get():
            # запомнить COM и заблокировать список
            cur = self.port_var.get()
            if cur and not is_url_port(cur):
                self._com_saved = cur
            self.port_var.set(SOCKET_PRESET)
            self.port_cb.configure(state='disabled')
        else:
            self.port_cb.configure(state='readonly')
            if self._com_saved:
                self.port_var.set(self._com_saved)
            elif self._port_devices and self._port_devices[0]:
                self.port_cb.current(0)
            else:
                self.port_var.set('')

    def _select_port(self, device: str, labels: list):
        """Выбрать COM по device или первый в списке."""
        if device and device in self._port_devices:
            idx = self._port_devices.index(device)
            self.port_cb.current(idx)
            return
        if labels and self._port_devices[0]:
            self.port_cb.current(0)
        else:
            self.port_var.set(labels[0] if labels else '')

    def _resolve_port(self) -> str | None:
        if self.socket_var.get():
            return SOCKET_PRESET
        text = self.port_var.get().strip()
        if text in self._port_devices:
            return text
        idx = self.port_cb.current()
        values = list(self.port_cb['values'] or [])
        if 0 <= idx < len(self._port_devices) and self._port_devices[idx]:
            if not text or (idx < len(values) and text == values[idx]):
                return self._port_devices[idx]
        if text and not text.startswith('('):
            return text.split()[0]
        return None

    def _ok(self):
        port = self._resolve_port()
        if not port:
            print("Не выбран COM-порт", file=sys.stderr, flush=True)
            messagebox.showerror("Ошибка", "Не выбран COM-порт или URL", parent=self.top)
            return
        self.result = {
            'port': port,
            'baud': int(self.baud_var.get()),
            'parity': PARITY_MAP[self.parity_var.get()],
            'stop': int(self.stop_var.get()),
        }
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()
