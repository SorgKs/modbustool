"""Точка входа: python __main__.py"""
import sys
import traceback
import tkinter as tk

from app import SnifferApp


def _print_exception(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
    sys.stderr.flush()


def main():
    sys.excepthook = _print_exception

    root = tk.Tk()
    root.report_callback_exception = _print_exception
    root.geometry("900x560")

    app = SnifferApp(root)
    if not app.ready:
        return
    root.mainloop()


if __name__ == '__main__':
    main()
