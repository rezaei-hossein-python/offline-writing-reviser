from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingCancelled,
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
    OfflineWritingProviderCancelled,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


class _UnsafeRevision(RuntimeError):
    """Internal signal to preserve one unsafe chunk."""


class OfflineWritingService:
    supports_progress = True

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
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        cancel_current = getattr(self.provider, "cancel_current", None)
        if callable(cancel_current):
            cancel_current()

    def revise(
        self,
        selected_text: str,
        progress: Callable[[str], None] | None = None,
    ) -> WritingRevisionResult:
        if not self.config.enabled:
            raise OfflineWritingInputError("Offline writing is disabled")
        if not selected_text or not selected_text.strip():
            raise OfflineWritingInputError("Selection is empty")
        if len(selected_text) > self.config.max_characters:
            raise OfflineWritingInputError("Selection exceeds maximum length")
        if not self._lock.acquire(blocking=False):
            raise OfflineWritingBusy("Offline writing revision already running")
        self._cancel_event.clear()
        progress = progress or (lambda _message: None)

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
            revised_chunks: list[str] = []
            original_chunks: list[str] = []
            chunk_durations: list[float] = []
            successful_chunks = 0
            preserved_chunks = 0
            timeout_chunks = 0
            unsafe_chunks = 0
            adaptive_target = self.config.chunk_characters
            for index, chunk in enumerate(chunks, start=1):
                self._raise_if_cancelled()
                progress(f"Revising section {index} of {len(chunks)}")
                prefix, content, suffix = _separate_outer_whitespace(chunk)
                if content:
                    revised_content, outcome, chunk_duration = self._revise_chunk(
                        content, index, len(chunks)
                    )
                else:
                    revised_content, outcome, chunk_duration = content, "preserved", 0.0
                assembled = prefix + revised_content + suffix
                revised_chunks.append(assembled)
                original_chunks.append(chunk)
                chunk_durations.append(chunk_duration)
                if outcome == "success":
                    successful_chunks += 1
                else:
                    preserved_chunks += 1
                    timeout_chunks += outcome == "timeout"
                    unsafe_chunks += outcome == "unsafe"
                if (
                    index < len(chunks)
                    and adaptive_target > 300
                    and chunk_duration >= self.config.timeout_seconds * 750
                ):
                    adaptive_target = max(300, adaptive_target // 2)
                    remaining: list[str] = []
                    for pending in chunks[index:]:
                        remaining.extend(
                            split_proofreading_chunks(pending, adaptive_target)
                        )
                    chunks[index:] = remaining
                    self.logger.info(
                        "Offline writing chunk target adapted target_chars=%s "
                        "remaining_chunks=%s",
                        adaptive_target,
                        len(remaining),
                    )
            revised_text, rolled_back = self._validate_reconstruction(
                selected_text, original_chunks, revised_chunks
            )
            if rolled_back:
                successful_chunks -= rolled_back
                preserved_chunks += rolled_back
                unsafe_chunks += rolled_back
            duration_ms = (time.perf_counter() - started) * 1000
            completion = (
                "Completed with some sections unchanged"
                if preserved_chunks
                else "Completed"
            )
            progress(completion)
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
                metadata={
                    "chunk_count": len(chunks),
                    "chunk_durations_ms": chunk_durations,
                    "successful_chunks": successful_chunks,
                    "preserved_chunks": preserved_chunks,
                    "timeout_chunks": timeout_chunks,
                    "unsafe_chunks": unsafe_chunks,
                    "completion_status": completion,
                },
            )
        except (
            OfflineWritingBusy,
            OfflineWritingInputError,
            OfflineWritingMalformedOutput,
            OfflineWritingProviderError,
            OfflineWritingProviderTimeout,
            OfflineWritingProviderUnavailable,
            OfflineWritingCancelled,
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

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise OfflineWritingCancelled("Revision was cancelled")

    def _revise_chunk(
        self, chunk: str, index: int, chunk_count: int
    ) -> tuple[str, str, float]:
        self.logger.info(
            "Offline writing Ollama invocation started chunk_index=%s "
            "chunk_count=%s chars=%s provider=%s model=%s",
            index,
            chunk_count,
            len(chunk),
            self.provider.provider_name,
            self.provider.model_identifier,
        )
        started = time.perf_counter()
        raw_output = ""
        for attempt in (1, 2):
            self._raise_if_cancelled()
            try:
                raw_output = self.provider.revise(
                    chunk,
                    REVISION_INSTRUCTION,
                    timeout_seconds=self.config.timeout_seconds,
                )
                break
            except OfflineWritingProviderCancelled as exc:
                raise OfflineWritingCancelled("Revision was cancelled") from exc
            except OfflineWritingProviderUnavailable:
                raise
            except OfflineWritingMalformedOutput as exc:
                self.logger.warning(
                    "Revision chunk rejected chunk_index=%s chunk_count=%s "
                    "category=%s rejection_reason=%s",
                    index,
                    chunk_count,
                    exc.__class__.__name__,
                    exc.reason,
                )
                return chunk, "unsafe", (time.perf_counter() - started) * 1000
            except OfflineWritingProviderTimeout:
                self.logger.warning(
                    "Offline writing chunk timed out chunk_index=%s "
                    "chunk_count=%s attempt=%s",
                    index,
                    chunk_count,
                    attempt,
                )
                if attempt == 2:
                    return chunk, "timeout", (time.perf_counter() - started) * 1000
            except OfflineWritingProviderError as exc:
                self.logger.warning(
                    "Offline writing chunk preserved chunk_index=%s "
                    "chunk_count=%s category=%s",
                    index,
                    chunk_count,
                    exc.__class__.__name__,
                )
                return chunk, "unsafe", (time.perf_counter() - started) * 1000
        try:
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
            if not _structure_preserved(chunk, revised):
                self.logger.warning(
                    "Revision chunk rejected chunk_index=%s chunk_count=%s "
                    "semantic_reasons=structure_changed",
                    index,
                    chunk_count,
                )
                raise _UnsafeRevision from None
        except OfflineWritingMalformedOutput as exc:
            self.logger.warning(
                "Revision chunk rejected chunk_index=%s chunk_count=%s "
                "category=%s rejection_reason=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
                exc.reason,
            )
            return chunk, "unsafe", (time.perf_counter() - started) * 1000
        except OfflineWritingProviderUnavailable as exc:
            self.logger.warning(
                "Offline writing chunk failed chunk_index=%s chunk_count=%s "
                "category=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
            )
            raise
        except _UnsafeRevision:
            return chunk, "unsafe", (time.perf_counter() - started) * 1000
        except OfflineWritingProviderError as exc:
            self.logger.warning(
                "Offline writing chunk preserved chunk_index=%s chunk_count=%s "
                "category=%s",
                index,
                chunk_count,
                exc.__class__.__name__,
            )
            return chunk, "unsafe", (time.perf_counter() - started) * 1000
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
        return revised, "success", (time.perf_counter() - started) * 1000

    def _validate_reconstruction(
        self,
        selected_text: str,
        original_chunks: list[str],
        revised_chunks: list[str],
    ) -> tuple[str, int]:
        candidate = "".join(revised_chunks)
        validation = validate_semantic_preservation(selected_text, candidate)
        if validation.accepted and meaning_anchor_preserved(selected_text, candidate):
            return candidate, 0
        rolled_back = 0
        for index in reversed(range(len(revised_chunks))):
            if revised_chunks[index] == original_chunks[index]:
                continue
            revised_chunks[index] = original_chunks[index]
            rolled_back += 1
            candidate = "".join(revised_chunks)
            validation = validate_semantic_preservation(selected_text, candidate)
            if validation.accepted and meaning_anchor_preserved(selected_text, candidate):
                self.logger.warning(
                    "Revision reconstruction accepted after_chunk_rollbacks=%s",
                    rolled_back,
                )
                return candidate, rolled_back
        self.logger.warning(
            "Revision reconstruction rejected; original preserved chars=%s",
            len(selected_text),
        )
        return selected_text, rolled_back


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


LINE_ENDING_PATTERN = re.compile(r"\r\n|\r|\n")
STRUCTURAL_PREFIX_PATTERN = re.compile(
    r"^[ \t]*(?:(?:[-*+] |\d+[.)] |#{1,6} |>[ \t]?))?"
)


def _structure_preserved(original: str, revised: str) -> bool:
    if LINE_ENDING_PATTERN.findall(original) != LINE_ENDING_PATTERN.findall(revised):
        return False
    original_lines = LINE_ENDING_PATTERN.split(original)
    revised_lines = LINE_ENDING_PATTERN.split(revised)
    if len(original_lines) != len(revised_lines):
        return False
    return all(
        STRUCTURAL_PREFIX_PATTERN.match(before).group(0)
        == STRUCTURAL_PREFIX_PATTERN.match(after).group(0)
        for before, after in zip(original_lines, revised_lines, strict=True)
    )
