"""Точка входа: python __main__.py"""
import sys
import traceback
import tkinter as tk

from app import SnifferApp
from settings_dialog import SettingsDialog


def _print_exception(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
    sys.stderr.flush()


def main():
    sys.excepthook = _print_exception

    root = tk.Tk()
    root.report_callback_exception = _print_exception

    dlg = SettingsDialog(root)
    dlg.wait()

    if dlg.result is None:
        root.destroy()
        return

    dlg.clear()
    app = SnifferApp(root, dlg.result)
    if not app.ready:
        return
    root.mainloop()


if __name__ == '__main__':
    main()
