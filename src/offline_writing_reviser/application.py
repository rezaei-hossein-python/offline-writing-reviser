from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.desktop_status import (
    ApplicationState,
    UserMessage,
    user_message_for_error,
)
from offline_writing_reviser.logging_config import configure_logging
from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProviderError,
)
from offline_writing_reviser.providers.ollama import OllamaCliOfflineWritingProvider
from offline_writing_reviser.settings import SettingsStore
from offline_writing_reviser.settings_ui import SettingsWindow
from offline_writing_reviser.tray import TrayIcon
from offline_writing_reviser.version import __version__
from offline_writing_reviser.windows.controller import (
    OfflineWritingRuntime,
    start_offline_writing_runtime,
)
from offline_writing_reviser.windows.single_instance import WindowsSingleInstance


APP_NAME = "Offline Writing Reviser"
MUTEX_NAME = r"Local\OfflineWritingReviserV1"


class DesktopCoordinator:
    def __init__(
        self,
        *,
        config: OfflineWritingConfig,
        settings_store: SettingsStore,
        runtime: OfflineWritingRuntime,
        stop_event: threading.Event,
        logger: logging.Logger,
    ):
        self.config = config
        self.settings_store = settings_store
        self.runtime = runtime
        self.stop_event = stop_event
        self.logger = logger
        self.restart_requested = False
        self.state = ApplicationState.READY
        self._settings_thread: threading.Thread | None = None
        self.settings_window = SettingsWindow(
            config_getter=lambda: self.config,
            save_callback=self.apply_settings,
            reset_callback=self.reset_settings,
            model_loader=self.discover_models,
            logger=logger,
        )
        self.tray = TrayIcon(
            on_revise=self.trigger_revision,
            on_settings=self.settings_window.show,
            on_open_logs=self.open_log_folder,
            on_restart=self.request_restart,
            on_exit=self.request_exit,
            logger=logger,
        )
        if getattr(runtime, "controller", None):
            runtime.controller.state_callback = self.set_state
            runtime.controller.notification_callback = self.notify

    def start(self) -> None:
        self.tray.start()
        self._settings_thread = threading.Thread(
            target=self.settings_window.run,
            args=(self.stop_event,),
            name="offline-writing-settings-ui",
            daemon=True,
        )
        self._settings_thread.start()
        if not self.runtime.has_registered_hotkeys:
            self.set_state(ApplicationState.HOTKEY_UNAVAILABLE)
            self.notify(user_message_for_error("hotkey"))
        threading.Thread(
            target=self.refresh_status,
            name="offline-writing-status-check",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.settings_window.close()
        if self._settings_thread:
            self._settings_thread.join(timeout=2)
            self._settings_thread = None
        self.tray.stop()

    def set_state(self, state: ApplicationState) -> None:
        self.state = state
        self.tray.set_state(state)
        self.logger.info("Application state changed state=%s", state.value)

    def notify(self, message: UserMessage) -> None:
        self.tray.notify(message)

    def trigger_revision(self) -> None:
        if self.runtime.controller:
            self.runtime.controller.trigger()

    def discover_models(self) -> list[str]:
        provider = OllamaCliOfflineWritingProvider(
            model=self.config.model,
            executable=self.config.ollama_executable,
        )
        models = provider.list_installed_models(timeout_seconds=5)
        self.logger.info(
            "Ollama availability available=true installed_model_count=%s configured_model_available=%s",
            len(models),
            self.config.model in models,
        )
        return models

    def refresh_status(self) -> None:
        if not self.runtime.has_registered_hotkeys:
            return
        try:
            models = self.discover_models()
        except OfflineWritingProviderError as exc:
            message = user_message_for_error(exc)
            self.set_state(message.state)
            self.notify(message)
            self.logger.warning(
                "Ollama availability available=false category=%s",
                exc.__class__.__name__,
            )
            return
        if self.config.model not in models:
            message = user_message_for_error(
                OfflineWritingModelMissing("Configured model is unavailable")
            )
            self.set_state(message.state)
            self.notify(message)
            return
        self.set_state(ApplicationState.READY)

    def apply_settings(self, requested: OfflineWritingConfig) -> OfflineWritingConfig:
        previous = self.config
        applied = requested
        if not self.runtime.apply_config(requested):
            if requested.hotkey != previous.hotkey:
                applied = replace(requested, hotkey=previous.hotkey)
                self.runtime.apply_config(applied)
                self.notify(user_message_for_error("hotkey"))
                self.set_state(ApplicationState.READY)
            else:
                raise RuntimeError("Settings could not be applied.")
        self.config = self.settings_store.save(applied)
        threading.Thread(
            target=self.refresh_status,
            name="offline-writing-status-refresh",
            daemon=True,
        ).start()
        return self.config

    def reset_settings(self) -> OfflineWritingConfig:
        defaults = replace(
            self.settings_store.defaults,
            ollama_executable=self.config.ollama_executable,
            log_file=self.config.log_file,
        )
        return self.apply_settings(defaults)

    def open_log_folder(self) -> None:
        folder = self.config.log_file.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError:
            self.logger.exception("Could not open log folder path=%s", folder)
            self.notify(
                UserMessage(
                    "Could not open log folder",
                    f"The logs are stored at {folder}",
                    ApplicationState.ERROR,
                )
            )

    def request_restart(self) -> None:
        self.restart_requested = True
        self.stop_event.set()

    def request_exit(self) -> None:
        self.stop_event.set()


class OfflineWritingReviserApplication:
    def __init__(
        self,
        config: OfflineWritingConfig | None = None,
        stop_event: threading.Event | None = None,
        instance: WindowsSingleInstance | None = None,
        wait_interval_seconds: float = 0.2,
        settings_store: SettingsStore | None = None,
    ):
        self.settings_store = settings_store or SettingsStore()
        self.config = config or self.settings_store.load()
        self.stop_event = stop_event or threading.Event()
        self.instance = instance or WindowsSingleInstance(MUTEX_NAME)
        self.wait_interval_seconds = wait_interval_seconds
        self.runtime: OfflineWritingRuntime | None = None
        self.desktop: DesktopCoordinator | None = None

    def run(self) -> int:
        if sys.platform != "win32":
            print(f"{APP_NAME} requires Windows.", file=sys.stderr)
            return 1
        if not self.instance.acquire():
            print(f"{APP_NAME} is already running.", file=sys.stderr)
            return 0

        configure_logging(self.config.log_file)
        logger = logging.getLogger("offline-writing-reviser")
        if self.settings_store.recovered_corrupt_file:
            logger.warning("Corrupt settings recovered with defaults")
        logger.info(
            "Application startup version=%s model=%s hotkey=%s log_file=%s",
            __version__,
            self.config.model,
            self.config.hotkey,
            self.config.log_file,
        )
        exit_code = 0
        restart_requested = False
        try:
            self.runtime = start_offline_writing_runtime(self.config, logger=logger)
            # A runtime with no controller is an unsupported-provider or test boundary.
            if not self.runtime.has_registered_hotkeys and not getattr(
                self.runtime, "controller", None
            ):
                logger.error("Application startup failed stage=hotkey_unavailable")
                print(
                    f"{APP_NAME} could not register {self.config.hotkey}. "
                    "Another application may already be using that hotkey.",
                    file=sys.stderr,
                )
                return 1
            _install_shutdown_handlers(self.stop_event)
            if not self.stop_event.is_set() and getattr(
                self.runtime, "controller", None
            ):
                self.desktop = DesktopCoordinator(
                    config=self.config,
                    settings_store=self.settings_store,
                    runtime=self.runtime,
                    stop_event=self.stop_event,
                    logger=logger,
                )
                self.desktop.start()
            logger.info("Application ready")
            while not self.stop_event.wait(timeout=self.wait_interval_seconds):
                pass
            restart_requested = bool(
                self.desktop and self.desktop.restart_requested
            )
        except Exception:
            logger.exception("Application failed")
            exit_code = 1
        finally:
            if self.desktop:
                self.desktop.stop()
            if self.runtime is not None:
                self.runtime.stop()
                logger.info("Hotkey runtime stopped")
            logger.info("Application shutdown")
            self.instance.release()

        if restart_requested:
            try:
                _start_replacement_process()
                logger.info("Application restart process launched")
            except OSError:
                logger.exception("Application restart failed")
                return 1
        return exit_code


def validate_startup(config: OfflineWritingConfig | None = None) -> int:
    try:
        store = SettingsStore()
        config = config or store.load()
        configure_logging(config.log_file)
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("offline-writing-reviser")
        logger.info(
            "Startup validation version=%s provider=%s model=%s hotkey=%s",
            __version__,
            config.provider,
            config.model,
            config.hotkey,
        )
    except Exception as exc:
        print(f"Startup validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"{APP_NAME} {__version__} startup validation passed.")
    return 0


def _start_replacement_process() -> None:
    if getattr(sys, "frozen", False):
        args = [sys.executable]
        working_directory = str(Path(sys.executable).resolve().parent)
    else:
        args = [sys.executable, "-m", "offline_writing_reviser"]
        working_directory = None
    subprocess.Popen(
        args,
        cwd=working_directory,
        close_fds=True,
    )


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    def request_stop(*_args) -> None:
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, request_stop)
    if sys.platform == "win32":
        try:
            ctypes = __import__("ctypes")
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)

            @handler_type
            def console_handler(_event) -> bool:
                stop_event.set()
                return True

            _install_shutdown_handlers._console_handler = console_handler
            ctypes.windll.kernel32.SetConsoleCtrlHandler(console_handler, True)
        except Exception:
            return
