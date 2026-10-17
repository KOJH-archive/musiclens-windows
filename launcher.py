"""Visible or background-safe launcher for MusicLens.

Any startup crash gets logged and shown in a native Windows dialog, including
when launched through pythonw.exe, which otherwise hides Python tracebacks.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import traceback
import threading

_mutex_handle = None
_activation_handle = None


def _single_instance() -> bool:
    """Keep the Windows named mutex alive for the lifetime of this process."""
    global _mutex_handle, _activation_handle
    import ctypes
    import getpass
    import hashlib

    key = hashlib.sha256(str(Path(__file__).resolve().parent).casefold().encode()).hexdigest()[:20]
    name = f"Local\\MusicLens_{getpass.getuser()}_{key}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    if not _mutex_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    already_running = ctypes.get_last_error() == 183
    event_name = name + "_activate"
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.SetEvent.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    if already_running:
        kernel32.OpenEventW.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.OpenEventW.restype = ctypes.c_void_p
        activation = kernel32.OpenEventW(0x0002, False, event_name)
        if activation:
            kernel32.SetEvent(activation)
            kernel32.CloseHandle(activation)
        return False
    _activation_handle = kernel32.CreateEventW(None, False, False, event_name)
    if not _activation_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def activation_requested() -> bool:
    if not _activation_handle:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    return kernel32.WaitForSingleObject(_activation_handle, 0) == 0

LOG_PATH = Path(__file__).resolve().parent / "musiclens-error.log"


def _configure_logging() -> None:
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=512_000, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger("musiclens").addHandler(handler)
    logging.getLogger("musiclens").setLevel(logging.INFO)
    def log_thread_error(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("musiclens").error(
            "Background thread failed: %s", args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    threading.excepthook = log_thread_error


def main() -> int:
    try:
        _configure_logging()
        if not _single_instance():
            logging.getLogger("musiclens").info("Another MusicLens instance is already running")
            return 0
        from app import MusicLens
        MusicLens(activation_requested=activation_requested).run()
        return 0
    except Exception:
        details = traceback.format_exc()
        logging.getLogger("musiclens").error("Application startup or runtime error:\n%s", details)
        print(details, file=sys.stderr, flush=True)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "MusicLens를 실행하지 못했습니다.\n\n"
                f"자세한 오류: {LOG_PATH}\n\n"
                + details[-950:],
                "MusicLens 실행 오류",
                0x10,
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
