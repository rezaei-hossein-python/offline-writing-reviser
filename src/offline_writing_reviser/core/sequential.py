from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingCancelled,
    OfflineWritingCorrectionUnavailable,
    OfflineWritingInputError,
    OfflineWritingMalformedOutput,
)
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionResult,
    LanguageToolCorrectionService,
)
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


PARAPHRASING_MODEL = "qwen3:1.7b"
AWKWARD_PHRASES = (
    "a number of different",
    "also need attention",
    "at this point in time",
    "difficult to follow",
    "for informing",
    "in order to let you know",
    "kind of hard to get through",
    "lesson contain",
    "made a big improvement",
    "more clear",
    "more time than it should take",
    "repeat the test again",
    "return back",
    "takes a long time",
    "longer than it needs to be",
    "very good and we discussed",
    "very good report",
    "very slow in nature",
)
SIMPLE_MECHANICAL_CATEGORIES = frozenset({"TYPOS", "PUNCTUATION", "CASING"})
LINE_ENDING_PATTERN = re.compile(r"\r\n|\r|\n")
STRUCTURAL_PREFIX_PATTERN = re.compile(
    r"^[ \t]*(?:(?:[-*+] |\d+[.)] |#{1,6} |>[ \t]?))?"
)


@dataclass(frozen=True)
class SequentialSectionResult:
    original_text: str
    languagetool_text: str
    paraphrased_text: str | None
    final_text: str
    qwen_invoked: bool
    qwen_accepted: bool
    fallback_reason: str | None
    languagetool_duration_ms: float
    languagetool_applied_edits: int
    languagetool_skipped_edits: int
    qwen_duration_ms: float
    validation_duration_ms: float
    qwen_first_token_ms: float | None = None


def should_invoke_paraphraser(
    corrected_text: str,
    correction: LanguageToolCorrectionResult,
) -> bool:
    """Small deterministic fast path; uncertainty defaults to paraphrasing."""
    lowered = corrected_text.casefold()
    if any(phrase in lowered for phrase in AWKWARD_PHRASES):
        return True
    applied = correction.applied_edits
    if not applied:
        return False
    categories = {edit.category.upper() for edit in applied}
    if categories <= SIMPLE_MECHANICAL_CATEGORIES:
        return False
    if len(applied) == 1 and applied[0].issue_type.casefold() == "grammar":
        return False
    return True


class SequentialWritingService:
    """One LanguageTool pass, optional one Qwen call, then original validation."""

    supports_progress = True

    def __init__(
        self,
        provider: OfflineWritingProvider,
        correction_service: LanguageToolCorrectionService,
        config: OfflineWritingConfig | None = None,
        logger: logging.Logger | None = None,
        section_splitter: Callable[[str, int], list[str]] | None = None,
    ):
        self.provider = provider
        self.correction_service = correction_service
        self.config = config or OfflineWritingConfig()
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self.section_splitter = section_splitter or split_production_sections
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        cancel = getattr(self.provider, "cancel_current", None)
        if callable(cancel):
            cancel()

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
        sections = self.section_splitter(
            selected_text, self.config.chunk_characters
        )
        results: list[SequentialSectionResult] = []
        try:
            for index, section in enumerate(sections, start=1):
                self._raise_if_cancelled()
                prefix, content, suffix = _separate_outer_whitespace(section)
                if not content:
                    results.append(
                        SequentialSectionResult(
                            section,
                            section,
                            None,
                            section,
                            False,
                            False,
                            None,
                            0.0,
                            0,
                            0,
                            0.0,
                            0.0,
                        )
                    )
                    continue
                result = self._revise_section(
                    content,
                    index,
                    len(sections),
                    progress,
                )
                results.append(
                    SequentialSectionResult(
                        original_text=prefix + result.original_text + suffix,
                        languagetool_text=prefix + result.languagetool_text + suffix,
                        paraphrased_text=(
                            None
                            if result.paraphrased_text is None
                            else prefix + result.paraphrased_text + suffix
                        ),
                        final_text=prefix + result.final_text + suffix,
                        qwen_invoked=result.qwen_invoked,
                        qwen_accepted=result.qwen_accepted,
                        fallback_reason=result.fallback_reason,
                        languagetool_duration_ms=result.languagetool_duration_ms,
                        languagetool_applied_edits=result.languagetool_applied_edits,
                        languagetool_skipped_edits=result.languagetool_skipped_edits,
                        qwen_duration_ms=result.qwen_duration_ms,
                        validation_duration_ms=result.validation_duration_ms,
                        qwen_first_token_ms=result.qwen_first_token_ms,
                    )
                )
            return self._build_result(selected_text, results, started, progress)
        finally:
            self._lock.release()

    def _revise_section(
        self,
        original: str,
        index: int,
        section_count: int,
        progress: Callable[[str], None],
    ) -> SequentialSectionResult:
        correction = self.correction_service.correct(original)
        if correction.failure is not None:
            raise OfflineWritingCorrectionUnavailable(
                "The private LanguageTool correction service is unavailable"
            )
        lt_text = correction.corrected_text
        applied = len(correction.applied_edits)
        skipped = len(correction.skipped_edits)
        if not should_invoke_paraphraser(lt_text, correction):
            return SequentialSectionResult(
                original,
                lt_text,
                None,
                lt_text,
                False,
                False,
                None,
                correction.duration_ms,
                applied,
                skipped,
                0.0,
                0.0,
            )

        progress(
            "Revising text"
            if section_count == 1
            else f"Revising section {index} of {section_count}"
        )
        qwen_started = time.perf_counter()
        raw_output: str | None = None
        try:
            revise_with_telemetry = getattr(
                self.provider, "revise_with_telemetry", None
            )
            if callable(revise_with_telemetry):
                inference = revise_with_telemetry(
                    lt_text,
                    REVISION_INSTRUCTION,
                    timeout_seconds=self.config.timeout_seconds,
                )
                raw_output = inference.text
                first_token_seconds = inference.telemetry.get(
                    "first_token_seconds"
                )
                first_token_ms = (
                    first_token_seconds * 1000
                    if isinstance(first_token_seconds, (int, float))
                    else None
                )
            else:
                raw_output = self.provider.revise(
                    lt_text,
                    REVISION_INSTRUCTION,
                    timeout_seconds=self.config.timeout_seconds,
                )
                first_token_ms = None
        except OfflineWritingProviderCancelled as exc:
            raise OfflineWritingCancelled("Revision was cancelled") from exc
        except OfflineWritingProviderTimeout:
            return self._fallback(
                original, lt_text, raw_output, correction, "qwen_timeout", qwen_started
            )
        except (OfflineWritingProviderUnavailable, OfflineWritingProviderError):
            return self._fallback(
                original,
                lt_text,
                raw_output,
                correction,
                "qwen_unavailable",
                qwen_started,
            )

        qwen_duration_ms = (time.perf_counter() - qwen_started) * 1000
        validation_started = time.perf_counter()
        try:
            candidate = sanitize_revision_output(raw_output, original_text=lt_text)
            candidate = _remove_added_trailing_whitespace(lt_text, candidate)
            candidate = restore_source_number_formatting(original, candidate)
            candidate = restore_source_word_casing(original, candidate)
            validation = validate_semantic_preservation(original, candidate)
            safe = (
                validation.accepted
                and meaning_anchor_preserved(original, candidate)
                and _structure_preserved(original, candidate)
            )
            accepted = safe and candidate != lt_text
            reason = None
            if not safe:
                reason = (
                    ",".join(validation.reasons)
                    or "meaning_or_structure_not_preserved"
                )
            elif not accepted:
                reason = "qwen_no_useful_change"
        except OfflineWritingMalformedOutput as exc:
            candidate = raw_output
            safe = False
            accepted = False
            reason = exc.reason
        validation_ms = (time.perf_counter() - validation_started) * 1000
        return SequentialSectionResult(
            original,
            lt_text,
            raw_output,
            candidate if accepted else lt_text,
            True,
            accepted,
            None if accepted else reason,
            correction.duration_ms,
            applied,
            skipped,
            qwen_duration_ms,
            validation_ms,
            first_token_ms,
        )

    @staticmethod
    def _fallback(
        original: str,
        lt_text: str,
        raw_output: str | None,
        correction: LanguageToolCorrectionResult,
        reason: str,
        started: float,
    ) -> SequentialSectionResult:
        return SequentialSectionResult(
            original,
            lt_text,
            raw_output,
            lt_text,
            True,
            False,
            reason,
            correction.duration_ms,
            len(correction.applied_edits),
            len(correction.skipped_edits),
            (time.perf_counter() - started) * 1000,
            0.0,
        )

    def _build_result(
        self,
        original: str,
        sections: list[SequentialSectionResult],
        started: float,
        progress: Callable[[str], None],
    ) -> WritingRevisionResult:
        lt_text = "".join(section.languagetool_text for section in sections)
        paraphrased = "".join(
            section.paraphrased_text
            if section.paraphrased_text is not None
            else section.languagetool_text
            for section in sections
        )
        final = "".join(section.final_text for section in sections)
        invoked = [section for section in sections if section.qwen_invoked]
        accepted = sum(section.qwen_accepted for section in invoked)
        rejected = len(invoked) - accepted
        unavailable = any(
            section.fallback_reason == "qwen_unavailable" for section in invoked
        )
        lt_changed = lt_text != original
        if unavailable:
            completion = (
                "AI paraphrasing unavailable; grammar corrections applied"
                if lt_changed
                else "AI paraphrasing unavailable; text unchanged"
            )
            category = "languagetool_fallback"
        elif rejected:
            completion = "Completed with some sections corrected but not paraphrased"
            category = "partial_paraphrase"
        elif accepted:
            completion = "Revised"
            category = "paraphrased"
        elif lt_changed:
            completion = "Corrected"
            category = "languagetool_only"
        else:
            completion = "Unchanged"
            category = "unchanged"
        if completion != "Unchanged":
            progress(completion)
        duration_ms = (time.perf_counter() - started) * 1000
        metadata = {
            "section_count": len(sections),
            "languagetool_duration_ms": sum(
                section.languagetool_duration_ms for section in sections
            ),
            "languagetool_applied_edit_count": sum(
                section.languagetool_applied_edits for section in sections
            ),
            "languagetool_skipped_edit_count": sum(
                section.languagetool_skipped_edits for section in sections
            ),
            "qwen_invoked": bool(invoked),
            "qwen_call_count": len(invoked),
            "qwen_duration_ms": sum(section.qwen_duration_ms for section in invoked),
            "qwen_first_token_ms": [
                section.qwen_first_token_ms
                for section in invoked
                if section.qwen_first_token_ms is not None
            ],
            "qwen_accepted_sections": accepted,
            "qwen_rejected_sections": rejected,
            "fallback_sections": rejected,
            "validation_duration_ms": sum(
                section.validation_duration_ms for section in invoked
            ),
            "result_category": category,
            "completion_status": completion,
        }
        self.logger.info(
            "Sequential revision processing input_chars=%s sections=%s "
            "lt_duration_ms=%.2f lt_applied=%s lt_skipped=%s qwen_invoked=%s "
            "qwen_duration_ms=%.2f qwen_accepted=%s qwen_rejected=%s "
            "fallback_sections=%s validation_ms=%.2f result_category=%s",
            len(original),
            metadata["section_count"],
            metadata["languagetool_duration_ms"],
            metadata["languagetool_applied_edit_count"],
            metadata["languagetool_skipped_edit_count"],
            metadata["qwen_invoked"],
            metadata["qwen_duration_ms"],
            accepted,
            rejected,
            rejected,
            metadata["validation_duration_ms"],
            category,
        )
        return WritingRevisionResult(
            original_character_count=len(original),
            revised_text=final,
            provider="languagetool+ollama_cli",
            model=self.provider.model_identifier,
            duration_ms=duration_ms,
            metadata=metadata,
            original_text=original,
            languagetool_text=lt_text,
            paraphrased_text=paraphrased,
            final_text=final,
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise OfflineWritingCancelled("Revision was cancelled")


def split_sequential_sections(text: str, target_characters: int) -> list[str]:
    """Pack complete paragraphs when safe; split oversized blocks at text boundaries."""
    if len(text) <= target_characters:
        return [text]
    boundaries = list(re.finditer(r"(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))+", text))
    blocks: list[str] = []
    start = 0
    for boundary in boundaries:
        blocks.append(text[start : boundary.end()])
        start = boundary.end()
    if start < len(text):
        blocks.append(text[start:])
    if not blocks:
        return split_proofreading_chunks(text, target_characters)
    sections: list[str] = []
    pending = ""
    for block in blocks:
        if len(block) > target_characters:
            if pending:
                sections.append(pending)
                pending = ""
            sections.extend(split_proofreading_chunks(block, target_characters))
        elif pending and len(pending) + len(block) > target_characters:
            sections.append(pending)
            pending = block
        else:
            pending += block
    if pending:
        sections.append(pending)
    if "".join(sections) != text:
        raise RuntimeError("Sequential chunking changed the source text")
    return sections


def split_production_sections(text: str, target_characters: int) -> list[str]:
    """Keep paragraph fallbacks independent and split only oversized paragraphs."""
    if target_characters < 1:
        raise ValueError("Chunk target must be positive")
    boundaries = list(
        re.finditer(r"(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))+", text)
    )
    if not boundaries:
        return split_proofreading_chunks(text, target_characters)
    blocks: list[str] = []
    start = 0
    for boundary in boundaries:
        blocks.append(text[start : boundary.end()])
        start = boundary.end()
    if start < len(text):
        blocks.append(text[start:])
    sections: list[str] = []
    for block in blocks:
        sections.extend(split_proofreading_chunks(block, target_characters))
    if "".join(sections) != text:
        raise RuntimeError("Production sectioning changed the source text")
    return sections


def _separate_outer_whitespace(value: str) -> tuple[str, str, str]:
    leading = re.match(r"^\s*", value).group(0)
    without_leading = value[len(leading) :]
    trailing = re.search(r"\s*$", without_leading).group(0)
    content = without_leading[: -len(trailing)] if trailing else without_leading
    return leading, content, trailing


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


def _remove_added_trailing_whitespace(source: str, candidate: str) -> str:
    source_lines = source.splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)
    if len(source_lines) != len(candidate_lines):
        return candidate
    cleaned: list[str] = []
    for source_line, candidate_line in zip(source_lines, candidate_lines, strict=True):
        ending_match = re.search(r"(?:\r\n|\r|\n)$", candidate_line)
        ending = ending_match.group(0) if ending_match else ""
        body = candidate_line[: -len(ending)] if ending else candidate_line
        source_ending_match = re.search(r"(?:\r\n|\r|\n)$", source_line)
        source_ending = source_ending_match.group(0) if source_ending_match else ""
        source_body = source_line[: -len(source_ending)] if source_ending else source_line
        if source_body == source_body.rstrip(" \t"):
            body = body.rstrip(" \t")
        cleaned.append(body + ending)
    return "".join(cleaned)
