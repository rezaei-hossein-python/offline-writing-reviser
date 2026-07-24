from __future__ import annotations

import logging
import threading
import time
import traceback

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.providers.base import OfflineWritingProviderError
from offline_writing_reviser.providers.ollama import OllamaCliOfflineWritingProvider
from offline_writing_reviser.windows.hotkeys import HotkeyBinding, WindowsHotkeyManager
from offline_writing_reviser.windows.text_selection import WindowsSelectedTextAdapter


class OfflineWritingController:
    def __init__(
        self,
        service: OfflineWritingService,
        text_adapter: WindowsSelectedTextAdapter,
        logger: logging.Logger | None = None,
    ):
        self.service = service
        self.text_adapter = text_adapter
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._lock = threading.Lock()

    def trigger(self) -> None:
        thread = threading.Thread(
            target=self._run_revision,
            name="offline-writing-revision",
            daemon=True,
        )
        thread.start()

    def _run_revision(self) -> None:
        started = time.perf_counter()
        if not self._lock.acquire(blocking=False):
            self.logger.info("Offline writing skipped category=busy")
            return
        try:
            try:
                capture = self.text_adapter.capture()
            except Exception as exc:
                self.logger.warning(
                    "Offline writing capture failed category=%s",
                    exc.__class__.__name__,
                )
                return
            if capture is None:
                self.logger.info("Offline writing skipped category=empty_selection")
                return
            try:
                result = self.service.revise(capture.text)
            except OfflineWritingProviderError as exc:
                self.logger.warning(
                    "Offline writing local provider failure category=%s",
                    exc.__class__.__name__,
                )
                return
            except ImportError as exc:
                self.logger.error(
                    "Offline writing import failure stage=revision "
                    "exception_type=%s message=%s traceback=%s",
                    exc.__class__.__name__,
                    exc,
                    "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__)).rstrip(),
                )
                return
            except Exception as exc:
                self.logger.warning(
                    "Offline writing failed category=%s",
                    exc.__class__.__name__,
                )
                return
            if not self.text_adapter.replace(capture, result.revised_text):
                self.logger.warning(
                    "Offline writing replacement skipped category=focus_changed"
                )
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info("Offline writing operation completed duration_ms=%.2f", duration_ms)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info("Offline writing total duration_ms=%.2f", duration_ms)
            self._lock.release()


class OfflineWritingRuntime:
    def __init__(self, hotkey_manager: WindowsHotkeyManager | None):
        self.hotkey_manager = hotkey_manager

    @property
    def has_registered_hotkeys(self) -> bool:
        return bool(self.hotkey_manager and self.hotkey_manager.registered_count)

    def stop(self) -> None:
        if self.hotkey_manager:
            self.hotkey_manager.stop()


def start_offline_writing_runtime(
    config: OfflineWritingConfig,
    logger: logging.Logger | None = None,
) -> OfflineWritingRuntime:
    logger = logger or logging.getLogger("offline-writing-reviser")
    if not config.enabled:
        logger.info("Offline writing hotkey disabled")
        return OfflineWritingRuntime(None)
    if config.provider != "ollama_cli":
        logger.error("Unsupported offline writing provider=%s", config.provider)
        return OfflineWritingRuntime(None)

    provider = OllamaCliOfflineWritingProvider(
        model=config.model,
        executable=config.ollama_executable,
    )
    service = OfflineWritingService(provider=provider, config=config, logger=logger)
    controller = OfflineWritingController(
        service=service,
        text_adapter=WindowsSelectedTextAdapter(logger=logger),
        logger=logger,
    )
    hotkey_manager = WindowsHotkeyManager(
        bindings=[
            HotkeyBinding(
                identifier=15001,
                shortcut=config.hotkey,
                callback=controller.trigger,
            )
        ],
        logger=logger,
    )
    hotkey_manager.start()
    return OfflineWritingRuntime(hotkey_manager)
