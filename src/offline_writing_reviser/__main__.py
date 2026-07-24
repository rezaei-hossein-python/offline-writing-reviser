from __future__ import annotations

import argparse
import sys

from offline_writing_reviser.application import APP_NAME, OfflineWritingReviserApplication, validate_startup
from offline_writing_reviser.version import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_NAME)
    parser.add_argument("--validate-startup", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.validate_startup:
        return validate_startup()
    _hide_private_console()
    return OfflineWritingReviserApplication().run()


def _hide_private_console() -> None:
    """Hide the console created by Explorer, but never hide a caller's terminal."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        process_ids = (ctypes.c_ulong * 8)()
        process_count = ctypes.windll.kernel32.GetConsoleProcessList(process_ids, 8)
        if process_count == 1:
            console_window = ctypes.windll.kernel32.GetConsoleWindow()
            if console_window:
                ctypes.windll.user32.ShowWindow(console_window, 0)
    except Exception:
        return


if __name__ == "__main__":
    raise SystemExit(main())
