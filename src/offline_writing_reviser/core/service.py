from __future__ import annotations

import logging
import re
import threading
import time

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingInputError,
    OfflineWritingMalformedOutput,
)
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
from offline_writing_reviser.proofreading.semantic import (
    meaning_anchor_preserved,
    restore_source_number_formatting,
    restore_source_word_casing,
    validate_semantic_preservation,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


class _UnsafeRevision(RuntimeError):
    """Internal signal to return the complete original selection unchanged."""


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
            chunks = split_proofreading_chunks(
                selected_text, self.config.chunk_characters
            )
            self.logger.info(
                "Offline writing chunk plan chunk_count=%s chunk_target_chars=%s",
                len(chunks),
                self.config.chunk_characters,
            )
            revised_chunks = []
            for index, chunk in enumerate(chunks, start=1):
                prefix, content, suffix = _separate_outer_whitespace(chunk)
                revised_content = (
                    self._revise_chunk(content, index, len(chunks))
                    if content
                    else content
                )
                revised_chunks.append(prefix + revised_content + suffix)
            revised_text = sanitize_revision_output(
                "".join(revised_chunks), original_text=selected_text
            )
            final_validation = validate_semantic_preservation(
                selected_text, revised_text
            )
            anchors_preserved = meaning_anchor_preserved(
                selected_text, revised_text
            )
            if not final_validation.accepted or not anchors_preserved:
                self.logger.warning(
                    "Revision rejected stage=final semantic_reasons=%s "
                    "meaning_anchor_preserved=%s chars=%s",
                    ",".join(final_validation.reasons) or "none",
                    anchors_preserved,
                    len(selected_text),
                )
                revised_text = selected_text
            duration_ms = (time.perf_counter() - started) * 1000
            if revised_text == selected_text:
                self.logger.info(
                    "Offline writing local revision completed "
                    "outcome=no_correction_required duration_ms=%.2f "
                    "provider=%s model=%s",
                    duration_ms,
                    self.provider.provider_name,
                    self.provider.model_identifier,
                )
            else:
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
        except _UnsafeRevision:
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.warning(
                "Offline writing revision rejected outcome=original_preserved "
                "chars=%s duration_ms=%.2f provider=%s model=%s",
                len(selected_text),
                duration_ms,
                self.provider.provider_name,
                self.provider.model_identifier,
            )
            return WritingRevisionResult(
                original_character_count=len(selected_text),
                revised_text=selected_text,
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

    def _revise_chunk(self, chunk: str, index: int, chunk_count: int) -> str:
        self.logger.info(
            "Offline writing Ollama invocation started chunk_index=%s "
            "chunk_count=%s chars=%s provider=%s model=%s",
            index,
            chunk_count,
            len(chunk),
            self.provider.provider_name,
            self.provider.model_identifier,
        )
        try:
            raw_output = self.provider.revise(
                chunk,
                REVISION_INSTRUCTION,
                timeout_seconds=self.config.timeout_seconds,
            )
            revised = sanitize_revision_output(raw_output, original_text=chunk)
            revised = restore_source_number_formatting(chunk, revised)
            revised = restore_source_word_casing(chunk, revised)
            validation = validate_semantic_preservation(chunk, revised)
            anchors_preserved = meaning_anchor_preserved(chunk, revised)
            if not validation.accepted or not anchors_preserved:
                self.logger.warning(
                    "Revision chunk rejected chunk_index=%s chunk_count=%s "
                    "semantic_reasons=%s meaning_anchor_preserved=%s",
                    index,
                    chunk_count,
                    ",".join(validation.reasons) or "none",
                    anchors_preserved,
                )
                raise _UnsafeRevision from None
        except OfflineWritingMalformedOutput as exc:
            self.logger.warning(
                "Revision chunk rejected chunk_index=%s chunk_count=%s "
                "category=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
            )
            raise _UnsafeRevision from exc
        except (
            OfflineWritingProviderError,
            OfflineWritingProviderTimeout,
            OfflineWritingProviderUnavailable,
        ) as exc:
            self.logger.warning(
                "Offline writing chunk failed chunk_index=%s chunk_count=%s "
                "category=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
            )
            raise
        except Exception as exc:
            self.logger.warning(
                "Offline writing chunk failed chunk_index=%s chunk_count=%s "
                "category=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
            )
            raise
        self.logger.info(
            "Offline writing Ollama invocation completed chunk_index=%s "
            "chunk_count=%s raw_chars=%s revised_chars=%s provider=%s model=%s",
            index,
            chunk_count,
            len(raw_output),
            len(revised),
            self.provider.provider_name,
            self.provider.model_identifier,
        )
        return revised


def _separate_outer_whitespace(value: str) -> tuple[str, str, str]:
    leading = re.match(r"^\s*", value).group(0)
    without_leading = value[len(leading) :]
    trailing = re.search(r"\s*$", without_leading).group(0)
    content = (
        without_leading[: -len(trailing)]
        if trailing
        else without_leading
    )
    return leading, content, trailing
