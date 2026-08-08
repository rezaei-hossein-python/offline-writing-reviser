from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.correction.languagetool import (
    LanguageToolRuntime,
    shared_languagetool_runtime,
)
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
from offline_writing_reviser.version import __version__
from offline_writing_reviser.windows.control import (
    ControlCommand,
    WindowsControlServer,
    send_control_command,
    wait_for_control_server,
    wait_for_control_server_stop,
)
from offline_writing_reviser.windows.controller import (
    OfflineWritingRuntime,
    start_offline_writing_runtime,
)
from offline_writing_reviser.windows.single_instance import (
    WindowsSingleInstance,
    wait_for_single_instance_stop,
)


APP_NAME = "Offline Writing Reviser"
MUTEX_NAME = r"Local\OfflineWritingReviserV1"
ERROR_DIALOG_COOLDOWN_SECONDS = 10.0


class BackgroundCoordinator:
    """Own the hidden settings host and user-intervention error dialogs."""

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
        self.state = ApplicationState.READY
        self._last_error_shown: dict[str, float] = {}
        self.settings_window = SettingsWindow(
            config_getter=lambda: self.config,
            save_callback=self.apply_settings,
            reset_callback=self.reset_settings,
            model_loader=self.discover_models,
            logger=logger,
        )
        if getattr(runtime, "controller", None):
            runtime.controller.state_callback = self.set_state
            runtime.controller.notification_callback = self.present_error

    def start(self) -> None:
        if not self.runtime.has_registered_hotkeys:
            self.set_state(ApplicationState.HOTKEY_UNAVAILABLE)
            self.present_error(
                UserMessage(
                    "Hotkey unavailable",
                    "The configured global hotkey could not be registered. "
                    "Open Settings with --settings and choose another hotkey.",
                    ApplicationState.HOTKEY_UNAVAILABLE,
                )
            )
        threading.Thread(
            target=self.refresh_status,
            name="offline-writing-status-check",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.settings_window.close()

    def run(self) -> None:
        """Run the GUI dispatcher on the main thread, hidden until requested."""
        self.settings_window.run(self.stop_event)

    def set_state(self, state: ApplicationState | str) -> None:
        self.state = state
        value = state.value if isinstance(state, ApplicationState) else state
        self.logger.info("Application state changed state=%s", value)
        self.settings_window.update_runtime_status(value)

    def present_error(self, message: UserMessage) -> None:
        """Show only actionable errors and rate-limit repeated dialogs."""
        now = time.monotonic()
        previous = self._last_error_shown.get(message.title, 0.0)
        if now - previous < ERROR_DIALOG_COOLDOWN_SECONDS:
            self.logger.info(
                "User error dialog suppressed category=%s cooldown=true",
                message.title,
            )
            return
        self._last_error_shown[message.title] = now
        self.settings_window.show_error(message)

    def discover_models(self) -> list[str]:
        provider = OllamaCliOfflineWritingProvider(
            model=self.config.model,
            executable=self.config.ollama_executable,
        )
        models = provider.list_installed_models(timeout_seconds=5)
        self.logger.info(
            "Ollama availability available=true installed_model_count=%s "
            "configured_model_available=%s",
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
            self.set_state(ApplicationState.OLLAMA_UNAVAILABLE)
            self.logger.warning(
                "Ollama availability available=false category=%s",
                exc.__class__.__name__,
            )
            return
        provider = OllamaCliOfflineWritingProvider(
            model=self.config.model,
            executable=self.config.ollama_executable,
        )
        if self.config.model not in models:
            message = user_message_for_error(
                OfflineWritingModelMissing(
                    f"Configured Ollama model is missing model={self.config.model}"
                )
            )
            if message.title != "AI model setup in progress":
                message = UserMessage(
                    "AI model setup required",
                    f"The AI model {self.config.model} is not installed yet. "
                    "Open Set up intelligent revision from the Start menu "
                    "to download it. Ctrl+Alt+P will remain unavailable "
                    "until setup verifies the model.",
                    ApplicationState.MODEL_UNAVAILABLE,
                )
            self.set_state(message.state)
            self.logger.warning(
                "Model status state=%s model=%s",
                "downloading" if message.title == "AI model setup in progress" else "missing",
                self.config.model,
            )
            self.present_error(message)
            return
        try:
            provider.ensure_api_running(timeout_seconds=20.0)
            runtime = provider.runtime_diagnostics(timeout_seconds=2.0)
            self.logger.info(
                "Ollama runtime model=%s loaded=%s acceleration=%s "
                "model_vram_bytes=%s context_length=%s backend=%s device=%s",
                self.config.model,
                runtime["model_loaded"],
                runtime["acceleration"],
                runtime["model_vram_bytes"],
                runtime["context_length"],
                runtime["backend"],
                runtime["device"],
            )
        except OfflineWritingProviderError as exc:
            self.set_state(ApplicationState.OLLAMA_UNAVAILABLE)
            self.logger.warning(
                "Ollama runtime unavailable category=%s; run --diagnostics",
                exc.__class__.__name__,
            )
            return
        self.set_state(ApplicationState.READY)
        self.logger.info("Model status state=ready model=%s", self.config.model)

    def apply_settings(self, requested: OfflineWritingConfig) -> OfflineWritingConfig:
        previous = self.config
        applied = requested
        if not self.runtime.apply_config(requested):
            if requested.hotkey != previous.hotkey:
                applied = replace(requested, hotkey=previous.hotkey)
                self.runtime.apply_config(applied)
                self.present_error(
                    UserMessage(
                        "Hotkey unavailable",
                        f"{requested.hotkey} could not be registered. "
                        f"The previous hotkey, {previous.hotkey}, remains active.",
                        ApplicationState.HOTKEY_UNAVAILABLE,
                    )
                )
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


class OfflineWritingReviserApplication:
    def __init__(
        self,
        config: OfflineWritingConfig | None = None,
        stop_event: threading.Event | None = None,
        instance: WindowsSingleInstance | None = None,
        wait_interval_seconds: float = 0.2,
        settings_store: SettingsStore | None = None,
        control_server_factory=WindowsControlServer,
        language_tool_runtime: LanguageToolRuntime | None = None,
    ):
        self.settings_store = settings_store or SettingsStore()
        self.config = config or self.settings_store.load()
        self.stop_event = stop_event or threading.Event()
        self.instance = instance or WindowsSingleInstance(MUTEX_NAME)
        self.wait_interval_seconds = wait_interval_seconds
        self.control_server_factory = control_server_factory
        self.runtime: OfflineWritingRuntime | None = None
        self.background: BackgroundCoordinator | None = None
        self.control_server: WindowsControlServer | None = None
        self.restart_requested = False
        self.language_tool_runtime = (
            language_tool_runtime or shared_languagetool_runtime()
        )

    def run(self) -> int:
        if sys.platform != "win32":
            print(f"{APP_NAME} requires Windows.", file=sys.stderr)
            return 1
        if not self.instance.acquire():
            _console_print(f"{APP_NAME} is already running.", error=True)
            return 0

        configure_logging(self.config.log_file)
        logger = logging.getLogger("offline-writing-reviser")
        if self.settings_store.recovered_corrupt_file:
            logger.warning("Corrupt settings recovered with defaults")
        logger.info(
            "Application startup version=%s model=%s revision_hotkey=%s "
            "log_file=%s desktop_mode=hidden",
            __version__,
            self.config.model,
            self.config.hotkey,
            self.config.log_file,
        )
        if self.settings_store.migrated_legacy_defaults:
            logger.info(
                "Application is using migrated default model model=%s",
                self.config.model,
            )
        exit_code = 0
        try:
            self.language_tool_runtime.logger = logger
            self.language_tool_runtime.start_in_background()
            self.runtime = start_offline_writing_runtime(self.config, logger=logger)
            if not self.runtime.has_registered_hotkeys and not getattr(
                self.runtime, "controller", None
            ):
                logger.error("Application startup failed stage=hotkey_unavailable")
                _console_print(
                    f"{APP_NAME} could not register {self.config.hotkey}. "
                    "Another application may already be using the hotkey.",
                    error=True,
                )
                return 1
            _install_shutdown_handlers(self.stop_event)
            if not self.stop_event.is_set() and getattr(
                self.runtime, "controller", None
            ):
                self.background = BackgroundCoordinator(
                    config=self.config,
                    settings_store=self.settings_store,
                    runtime=self.runtime,
                    stop_event=self.stop_event,
                    logger=logger,
                )
                self.background.start()
                self.control_server = self.control_server_factory(
                    on_settings=self.background.settings_window.show,
                    on_exit=self.request_exit,
                    on_restart=self.request_restart,
                    logger=logger,
                )
                self.control_server.start()
            logger.info("Application ready mode=hidden_background")
            if self.background:
                self.background.run()
            else:
                while not self.stop_event.wait(timeout=self.wait_interval_seconds):
                    pass
        except Exception:
            logger.exception("Application failed")
            exit_code = 1
        finally:
            if self.control_server:
                self.control_server.stop()
            if self.background:
                self.background.stop()
            if self.runtime is not None:
                self.runtime.stop()
                logger.info("Hotkey runtime stopped")
            self.language_tool_runtime.stop()
            logger.info("Application shutdown")
            self.instance.release()

        if self.restart_requested:
            try:
                _start_background_process()
                logger.info("Application restart process launched")
            except OSError:
                logger.exception("Application restart failed")
                return 1
        return exit_code

    def request_exit(self) -> None:
        self.stop_event.set()

    def request_restart(self) -> None:
        self.restart_requested = True
        self.stop_event.set()


def execute_control_command(command: ControlCommand) -> int:
    if sys.platform != "win32":
        _console_print(
            f"{APP_NAME} control commands require Windows.", error=True
        )
        return 1
    if send_control_command(command):
        if command is ControlCommand.EXIT:
            wait_for_control_server_stop()
            wait_for_single_instance_stop(MUTEX_NAME)
        return 0
    if command is ControlCommand.EXIT:
        _console_print(f"{APP_NAME} is not running.")
        return 0

    try:
        _start_background_process()
    except OSError as exc:
        _console_print(f"Could not start {APP_NAME}: {exc}", error=True)
        return 1
    if not wait_for_control_server():
        _console_print(
            f"{APP_NAME} did not start its control endpoint.", error=True
        )
        return 1
    if command is ControlCommand.SETTINGS and not send_control_command(command):
        _console_print("Could not open Settings.", error=True)
        return 1
    return 0


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
        _console_print(f"Startup validation failed: {exc}", error=True)
        return 1
    _console_print(f"{APP_NAME} {__version__} startup validation passed.")
    return 0


def _console_print(message: str, *, error: bool = False) -> None:
    """Write when attached; windowed production builds intentionally are not."""
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(message, file=stream)


def _start_background_process() -> None:
    environment = None
    if getattr(sys, "frozen", False):
        args = [sys.executable]
        working_directory = str(Path(sys.executable).resolve().parent)
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        args = [sys.executable, "-m", "offline_writing_reviser"]
        working_directory = None
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    subprocess.Popen(
        args,
        cwd=working_directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        env=environment,
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
