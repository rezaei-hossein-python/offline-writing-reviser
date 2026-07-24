from __future__ import annotations

import logging
import threading
import time

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingInputError,
    OfflineWritingMalformedOutput,
)
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
from offline_writing_reviser.providers.base import (
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


class OfflineWritingService:
    def __init__(
        self,
        provider: OfflineWritingProvider,
        config: OfflineWritingConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        self.provider = provider
        self.config = config or OfflineWritingConfig()
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._lock = threading.Lock()

    def revise(self, selected_text: str) -> WritingRevisionResult:
        if not self.config.enabled:
            raise OfflineWritingInputError("Offline writing is disabled")
        if not selected_text or not selected_text.strip():
            raise OfflineWritingInputError("Selection is empty")
        if len(selected_text) > self.config.max_characters:
            raise OfflineWritingInputError("Selection exceeds maximum length")
        if not self._lock.acquire(blocking=False):
            raise OfflineWritingBusy("Offline writing revision already running")

        started = time.perf_counter()
        self.logger.info(
            "Offline writing local revision started chars=%s provider=%s model=%s",
            len(selected_text),
            self.provider.provider_name,
            self.provider.model_identifier,
        )
        try:
            if not self.provider.is_available():
                raise OfflineWritingProviderUnavailable("Local writing provider unavailable")
            self.logger.info(
                "Offline writing Ollama invocation started provider=%s model=%s",
                self.provider.provider_name,
                self.provider.model_identifier,
            )
            raw_output = self.provider.revise(
                selected_text,
                REVISION_INSTRUCTION,
                timeout_seconds=self.config.timeout_seconds,
            )
            self.logger.info(
                "Offline writing Ollama invocation completed raw_chars=%s provider=%s model=%s",
                len(raw_output),
                self.provider.provider_name,
                self.provider.model_identifier,
            )
            revised_text = sanitize_revision_output(raw_output, original_text=selected_text)
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "Offline writing local revision succeeded original_chars=%s "
                "revised_chars=%s duration_ms=%.2f provider=%s model=%s",
                len(selected_text),
                len(revised_text),
                duration_ms,
                self.provider.provider_name,
                self.provider.model_identifier,
            )
            return WritingRevisionResult(
                original_character_count=len(selected_text),
                revised_text=revised_text,
                provider=self.provider.provider_name,
                model=self.provider.model_identifier,
                duration_ms=duration_ms,
            )
        except (
            OfflineWritingBusy,
            OfflineWritingInputError,
            OfflineWritingMalformedOutput,
            OfflineWritingProviderError,
            OfflineWritingProviderTimeout,
            OfflineWritingProviderUnavailable,
        ):
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.warning(
                "Offline writing revision failed category=%s chars=%s "
                "duration_ms=%.2f provider=%s model=%s",
                "local_failure",
                len(selected_text),
                duration_ms,
                self.provider.provider_name,
                self.provider.model_identifier,
            )
            raise
        finally:
            self._lock.release()
