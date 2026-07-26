from __future__ import annotations

import argparse
import sys

from offline_writing_reviser.application import (
    APP_NAME,
    OfflineWritingReviserApplication,
    execute_control_command,
    validate_startup,
)
from offline_writing_reviser.version import __version__
from offline_writing_reviser.windows.control import ControlCommand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_NAME)
    parser.add_argument("--validate-startup", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument(
        "--diagnostics-json",
        action="store_true",
        help="Emit diagnostics as JSON instead of a human-readable report.",
    )
    parser.add_argument(
        "--gemma-test",
        action="store_true",
        help="Include a small local Gemma proofreading health test.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument("--settings", action="store_true")
    controls.add_argument("--exit", action="store_true", dest="exit_application")
    controls.add_argument("--restart", action="store_true")
    controls.add_argument("--provision-model", action="store_true")
    args = parser.parse_args(argv)
    if args.gemma_test and not (args.diagnostics or args.diagnostics_json):
        parser.error("--gemma-test requires --diagnostics or --diagnostics-json")
    if args.validate_startup:
        return validate_startup()
    if args.diagnostics or args.diagnostics_json:
        from offline_writing_reviser.diagnostics import (
            collect_diagnostics,
            diagnostics_json,
            format_diagnostics,
        )

        report, healthy = collect_diagnostics(
            include_gemma_test=args.gemma_test
        )
        print(
            diagnostics_json(report)
            if args.diagnostics_json
            else format_diagnostics(report)
        )
        return 0 if healthy else 1
    if args.provision_model:
        _hide_private_console()
        from offline_writing_reviser.provisioning import run_model_provisioning

        return run_model_provisioning()
    if args.settings or args.exit_application or args.restart:
        _hide_private_console()
        if args.settings:
            return execute_control_command(ControlCommand.SETTINGS)
        if args.exit_application:
            return execute_control_command(ControlCommand.EXIT)
        return execute_control_command(ControlCommand.RESTART)
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
