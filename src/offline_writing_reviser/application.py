from __future__ import annotations

import logging
import signal
import sys
import threading

from offline_writing_reviser.config import OfflineWritingConfig, load_config_from_env
from offline_writing_reviser.logging_config import configure_logging
from offline_writing_reviser.windows.controller import start_offline_writing_runtime
from offline_writing_reviser.windows.single_instance import WindowsSingleInstance


APP_NAME = "Offline Writing Reviser"
MUTEX_NAME = r"Local\OfflineWritingReviserV1"


class OfflineWritingReviserApplication:
    def __init__(
        self,
        config: OfflineWritingConfig | None = None,
        stop_event: threading.Event | None = None,
        instance: WindowsSingleInstance | None = None,
        wait_interval_seconds: float = 1.0,
    ):
        self.config = config or load_config_from_env()
        self.stop_event = stop_event or threading.Event()
        self.instance = instance or WindowsSingleInstance(MUTEX_NAME)
        self.wait_interval_seconds = wait_interval_seconds
        self.runtime = None

    def run(self) -> int:
        if sys.platform != "win32":
            print("Offline Writing Reviser requires Windows.", file=sys.stderr)
            return 1
        if not self.instance.acquire():
            print("Offline Writing Reviser is already running.", file=sys.stderr)
            return 0

        configure_logging(self.config.log_file)
        logger = logging.getLogger("offline-writing-reviser")
        logger.info("Offline writing background process starting")
        try:
            self.runtime = start_offline_writing_runtime(self.config, logger=logger)
            if not self.runtime.has_registered_hotkeys:
                message = (
                    "Offline Writing Reviser could not register Ctrl+Alt+W. "
                    "Another application may already be using that hotkey."
                )
                logger.error("Offline writing background startup failed stage=hotkey_unavailable")
                print(message, file=sys.stderr)
                return 1
            _install_shutdown_handlers(self.stop_event)
            logger.info("Offline writing background process ready")
            while not self.stop_event.wait(timeout=self.wait_interval_seconds):
                pass
            return 0
        except Exception:
            logger.exception("Offline writing background process failed")
            return 1
        finally:
            if self.runtime is not None:
                self.runtime.stop()
                logger.info("Offline writing background runtime stopped")
            self.instance.release()


def validate_startup(config: OfflineWritingConfig | None = None) -> int:
    config = config or load_config_from_env()
    configure_logging(config.log_file)
    logger = logging.getLogger("offline-writing-reviser")
    logger.info(
        "Offline writing startup validation provider=%s model=%s hotkey=%s",
        config.provider,
        config.model,
        config.hotkey,
    )
    return 0


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    def request_stop(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    if sys.platform == "win32":
        try:
            handler_type = __import__("ctypes").WINFUNCTYPE(__import__("ctypes").c_bool, __import__("ctypes").c_ulong)

            @handler_type
            def console_handler(_event) -> bool:
                stop_event.set()
                return True

            _install_shutdown_handlers._console_handler = console_handler
            __import__("ctypes").windll.kernel32.SetConsoleCtrlHandler(console_handler, True)
        except Exception:
            return
