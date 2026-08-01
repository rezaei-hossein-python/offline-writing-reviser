from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.desktop_status import (
    ApplicationState,
    UserMessage,
    user_message_for_error,
)
from offline_writing_reviser.core.errors import (
    OfflineWritingCancelled,
    OfflineWritingError,
)
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderError,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.providers.ollama import OllamaCliOfflineWritingProvider
from offline_writing_reviser.windows.hotkeys import HotkeyBinding, WindowsHotkeyManager
from offline_writing_reviser.windows.text_selection import (
    CaptureFailure,
    SelectionCaptureError,
    SelectionTarget,
    WindowsSelectedTextAdapter,
)


class OfflineWritingController:
    def __init__(
        self,
        service: OfflineWritingService,
        text_adapter: WindowsSelectedTextAdapter,
        logger: logging.Logger | None = None,
        state_callback: Callable[[ApplicationState], None] | None = None,
        notification_callback: Callable[[UserMessage], None] | None = None,
    ):
        self.service = service
        self.text_adapter = text_adapter
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self.state_callback = state_callback or (lambda _state: None)
        self.notification_callback = notification_callback or (lambda _message: None)
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()

    def trigger(self) -> None:
        mode = "revision"
        if self._shutdown_event.is_set():
            self.logger.info(
                "Offline writing skipped mode=%s category=shutdown", mode
            )
            return
        try:
            target = self.text_adapter.capture_target(mode)
        except SelectionCaptureError as exc:
            self.logger.warning(
                "Offline writing target capture failed failure_code=%s",
                exc.failure.value,
            )
            self._report_error("capture_failed")
            return
        thread = threading.Thread(
            target=self._run_revision_thread,
            args=(mode, target),
            name=f"offline-writing-{mode}",
            daemon=True,
        )
        with self._threads_lock:
            self._threads.add(thread)
        thread.start()

    def stop(self, timeout_seconds: float = 3.0) -> None:
        self._shutdown_event.set()
        cancel = getattr(self.service, "cancel", None)
        if callable(cancel):
            cancel()
        with self._threads_lock:
            threads = list(self._threads)
        deadline = time.monotonic() + timeout_seconds
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _run_revision_thread(
        self,
        mode: str = "revision",
        target: SelectionTarget | None = None,
    ) -> None:
        try:
            self._run_revision(mode, target)
        finally:
            with self._threads_lock:
                self._threads.discard(threading.current_thread())

    def _run_revision(
        self,
        mode: str = "revision",
        target: SelectionTarget | None = None,
    ) -> None:
        started = time.perf_counter()
        if not self._lock.acquire(blocking=False):
            self.logger.info("Offline writing skipped category=busy")
            return
        try:
            self.state_callback(ApplicationState.REVISING)
            self.logger.info("Revision begin mode=%s", mode)
            try:
                capture = (
                    self.text_adapter.capture(target, mode)
                    if target is not None
                    else self.text_adapter.capture()
                )
            except SelectionCaptureError as exc:
                self.logger.warning(
                    "Offline writing capture failed failure_code=%s",
                    exc.failure.value,
                )
                self._report_error(
                    "no_selection"
                    if exc.failure is CaptureFailure.NO_SELECTION
                    else "capture_failed"
                )
                return
            except Exception as exc:
                self.logger.exception(
                    "Offline writing capture failed category=%s",
                    exc.__class__.__name__,
                )
                self._report_error(exc)
                return
            if capture is None:
                self._report_error("no_selection")
                return
            processing_started = time.perf_counter()
            if hasattr(self.text_adapter, "mark_processing"):
                self.text_adapter.mark_processing(capture)
            try:
                result = (
                    self.service.revise(capture.text, progress=self._progress)
                    if getattr(self.service, "supports_progress", False)
                    else self.service.revise(capture.text)
                )
            except OfflineWritingCancelled as exc:
                self.logger.info("Offline writing cancelled")
                if hasattr(self.text_adapter, "complete_without_replacement"):
                    self.text_adapter.complete_without_replacement(capture)
                self._progress("Cancelled")
                self.state_callback(ApplicationState.READY)
                return
            except OfflineWritingProviderError as exc:
                self.logger.warning(
                    "Offline writing local provider failure category=%s",
                    exc.__class__.__name__,
                )
                if hasattr(self.text_adapter, "complete_without_replacement"):
                    self.text_adapter.complete_without_replacement(capture)
                if isinstance(exc, OfflineWritingProviderUnavailable):
                    self._progress("AI unavailable")
                self._report_error(exc)
                return
            except ImportError as exc:
                self.logger.error(
                    "Offline writing import failure stage=revision "
                    "exception_type=%s message=%s traceback=%s",
                    exc.__class__.__name__,
                    exc,
                    "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__)).rstrip(),
                )
                self._report_error(exc)
                return
            except OfflineWritingError as exc:
                self.logger.warning(
                    "Offline writing failed category=%s",
                    exc.__class__.__name__,
                )
                self._report_error(exc)
                return
            except Exception as exc:
                self.logger.exception(
                    "Offline writing unexpected failure category=%s",
                    exc.__class__.__name__,
                )
                self._report_error(exc)
                return
            finally:
                if hasattr(self.text_adapter, "mark_processing"):
                    self.text_adapter.mark_processing(
                        capture,
                        (time.perf_counter() - processing_started) * 1000,
                    )
            if self._shutdown_event.is_set():
                self.logger.info(
                    "Offline writing replacement skipped category=shutdown"
                )
                return
            if result.revised_text == capture.text:
                if hasattr(self.text_adapter, "complete_without_replacement"):
                    self.text_adapter.complete_without_replacement(capture)
                self.state_callback(ApplicationState.READY)
                return
            if not self.text_adapter.replace(capture, result.revised_text):
                failure_code = (
                    getattr(capture, "operation", None).failure_code
                    if getattr(capture, "operation", None) is not None
                    else ""
                )
                self.logger.warning(
                    "Offline writing replacement skipped category=%s",
                    failure_code or "focus_changed",
                )
                self._report_error(
                    "clipboard_busy"
                    if failure_code == CaptureFailure.CLIPBOARD_BUSY.value
                    else "paste_failed"
                    if failure_code == CaptureFailure.PASTE_FAILED.value
                    else "focus_changed"
                )
                return
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "Revision end mode=%s outcome=success input_chars=%s "
                "output_chars=%s duration_ms=%.2f",
                mode,
                len(capture.text),
                len(result.revised_text),
                duration_ms,
            )
            self.state_callback(ApplicationState.READY)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "Offline writing total mode=%s duration_ms=%.2f",
                mode,
                duration_ms,
            )
            self._lock.release()

    def _report_error(self, error: BaseException | str) -> None:
        message = user_message_for_error(error)
        self.state_callback(message.state)
        self.notification_callback(message)

    def _progress(self, message: str) -> None:
        self.logger.info("Revision progress status=%s", message)
        self.state_callback(message)


class OfflineWritingRuntime:
    def __init__(
        self,
        hotkey_manager: WindowsHotkeyManager | None,
        controller: OfflineWritingController | None = None,
        config: OfflineWritingConfig | None = None,
        logger: logging.Logger | None = None,
        hotkey_manager_factory: Callable[..., WindowsHotkeyManager] = WindowsHotkeyManager,
    ):
        self.hotkey_manager = hotkey_manager
        self.controller = controller
        self.config = config or OfflineWritingConfig()
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self.hotkey_manager_factory = hotkey_manager_factory

    @property
    def has_registered_hotkeys(self) -> bool:
        if not self.hotkey_manager:
            return False
        return bool(
            getattr(
                self.hotkey_manager,
                "all_registered",
                self.hotkey_manager.registered_count,
            )
        )

    def stop(self) -> None:
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        if self.controller:
            self.controller.stop()

    def apply_config(self, config: OfflineWritingConfig) -> bool:
        """Apply settings, preserving a working old hotkey if replacement fails."""
        if not self.controller:
            self.config = config
            return False
        if config.hotkey != self.config.hotkey:
            candidate = self.hotkey_manager_factory(
                bindings=[
                    HotkeyBinding(
                        identifier=15002,
                        shortcut=config.hotkey,
                        callback=self.controller.trigger,
                    ),
                ],
                logger=self.logger,
            )
            candidate.start()
            if not candidate.registered_count:
                candidate.stop()
                self.logger.error(
                    "Hotkey settings change rejected requested=%s preserved=%s",
                    config.hotkey,
                    self.config.hotkey,
                )
                return False
            previous = self.hotkey_manager
            self.hotkey_manager = candidate
            if previous:
                previous.stop()
            self.logger.info(
                "Hotkey settings change applied old=%s new=%s",
                self.config.hotkey,
                config.hotkey,
            )
        provider = OllamaCliOfflineWritingProvider(
            model=config.model,
            executable=config.ollama_executable,
        )
        self.controller.service = OfflineWritingService(
            provider=provider,
            config=config,
            logger=self.logger,
        )
        self.config = config
        return True


def start_offline_writing_runtime(
    config: OfflineWritingConfig,
    logger: logging.Logger | None = None,
    state_callback: Callable[[ApplicationState], None] | None = None,
    notification_callback: Callable[[UserMessage], None] | None = None,
) -> OfflineWritingRuntime:
    logger = logger or logging.getLogger("offline-writing-reviser")
    if not config.enabled:
        logger.info("Offline writing hotkey disabled")
        return OfflineWritingRuntime(None, config=config, logger=logger)
    if config.provider != "ollama_cli":
        logger.error("Unsupported offline writing provider=%s", config.provider)
        return OfflineWritingRuntime(None, config=config, logger=logger)

    service = build_production_service(config, logger=logger)
    controller = OfflineWritingController(
        service=service,
        text_adapter=WindowsSelectedTextAdapter(logger=logger),
        logger=logger,
        state_callback=state_callback,
        notification_callback=notification_callback,
    )
    hotkey_manager = WindowsHotkeyManager(
        bindings=[
            HotkeyBinding(
                identifier=15001,
                shortcut=config.hotkey,
                callback=controller.trigger,
            ),
        ],
        logger=logger,
    )
    hotkey_manager.start()
    return OfflineWritingRuntime(
        hotkey_manager,
        controller=controller,
        config=config,
        logger=logger,
    )


def build_production_service(
    config: OfflineWritingConfig,
    logger: logging.Logger | None = None,
) -> OfflineWritingService:
    """Construct the exact service graph used by the installed application."""
    logger = logger or logging.getLogger("offline-writing-reviser")
    provider = OllamaCliOfflineWritingProvider(
        model=config.model,
        executable=config.ollama_executable,
    )
    return OfflineWritingService(
        provider=provider,
        config=config,
        logger=logger,
    )
