from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingInputError,
    OfflineWritingMalformedOutput,
)
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.providers.base import (
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


PARAPHRASE_INSTRUCTION = """Paraphrase the supplied text intentionally.

Preserve its meaning, language, names, numbers, dates, and factual content.
Improve clarity, readability, and natural wording. You may restructure
sentences, but do not invent facts or add unrelated material.

Preserve paragraph boundaries and blank lines where practical. Preserve source
markdown only when the source contains markdown.

Return only the revised text. Do not add commentary, labels, explanations,
quotation wrappers, or markdown fences."""

COMMENTARY_PREFIX = re.compile(
    r"^\s*(?:here(?:'s| is)(?:\s+the)?\s+(?:paraphrased|revised)\s+text|"
    r"paraphrased(?: text)?|revised(?: text)?|"
    r"revision|output|result)\s*[:\-]",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:[$£€¥])?\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w])"
)
CAPITALIZED_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
ACRONYM_PATTERN = re.compile(r"\b[A-Z][A-Z0-9&.-]{1,}\b")
MARKDOWN_FENCE = re.compile(r"^\s*```|```\s*$", re.MULTILINE)
MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
PARAGRAPH_BREAK = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)")


class ParaphraseService:
    """Intentional local rewriting with paraphrase-specific validation."""

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
            "Paraphrase route started chars=%s provider=%s model=%s",
            len(selected_text),
            self.provider.provider_name,
            self.provider.model_identifier,
        )
        try:
            if not self.provider.is_available():
                raise OfflineWritingProviderUnavailable(
                    "Local writing provider unavailable"
                )
            chunks = split_proofreading_chunks(
                selected_text, self.config.chunk_characters
            )
            revised_chunks: list[str] = []
            for index, chunk in enumerate(chunks, start=1):
                prefix, content, suffix = _separate_outer_whitespace(chunk)
                if not content:
                    revised_chunks.append(chunk)
                    continue
                raw_output = self.provider.revise(
                    content,
                    PARAPHRASE_INSTRUCTION,
                    timeout_seconds=self.config.timeout_seconds,
                )
                validation = validate_paraphrase_output(content, raw_output)
                self.logger.info(
                    "Paraphrase chunk validation chunk_index=%s "
                    "chunk_count=%s accepted=%s rejection_reasons=%s",
                    index,
                    len(chunks),
                    validation["accepted"],
                    ",".join(validation["reasons"]) or "none",
                )
                if not validation["accepted"]:
                    raise OfflineWritingMalformedOutput(
                        "Local paraphrase response failed validation"
                    )
                revised_chunks.append(
                    prefix
                    + _match_line_endings(
                        validation["output"], selected_text
                    )
                    + suffix
                )
            revised_text = "".join(revised_chunks)
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "Paraphrase route completed chars=%s revised_chars=%s "
                "duration_ms=%.2f provider=%s model=%s",
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
                metadata={"mode": "paraphrase"},
            )
        except (
            OfflineWritingBusy,
            OfflineWritingInputError,
            OfflineWritingMalformedOutput,
            OfflineWritingProviderError,
            OfflineWritingProviderTimeout,
            OfflineWritingProviderUnavailable,
        ):
            self.logger.warning(
                "Paraphrase route failed category=local_failure chars=%s "
                "duration_ms=%.2f",
                len(selected_text),
                (time.perf_counter() - started) * 1000,
            )
            raise
        finally:
            self._lock.release()


def validate_paraphrase_output(
    source: str, output: str | None
) -> dict[str, object]:
    """Validate intentional rewriting without imposing lexical locality."""
    reasons: list[str] = []
    candidate = _clean_control_characters(output or "")
    stripped = candidate.strip()

    if not stripped:
        reasons.append("empty_output")
    if "\x00" in (output or ""):
        reasons.append("invalid_control_character")
    if stripped and COMMENTARY_PREFIX.match(stripped):
        reasons.append("commentary")
    if stripped and not MARKDOWN_FENCE.search(source) and MARKDOWN_FENCE.search(
        stripped
    ):
        reasons.append("unexpected_markdown_wrapper")
    if stripped and not MARKDOWN_HEADING.search(source) and MARKDOWN_HEADING.search(
        stripped
    ):
        reasons.append("unexpected_markdown_heading")

    source_urls = set(URL_PATTERN.findall(source))
    output_urls = set(URL_PATTERN.findall(stripped))
    if output_urls - source_urls:
        reasons.append("hallucinated_url")

    source_numbers = Counter(NUMBER_PATTERN.findall(source))
    output_numbers = Counter(NUMBER_PATTERN.findall(stripped))
    if source_numbers != output_numbers:
        reasons.append("numbers_not_preserved")

    source_names = _detect_names(source)
    output_names = {value.casefold() for value in _detect_names(stripped)}
    if any(value.casefold() not in output_names for value in source_names):
        reasons.append("names_not_preserved")

    source_paragraphs = _paragraph_count(source)
    output_paragraphs = _paragraph_count(stripped)
    if source_paragraphs > 1 and output_paragraphs < source_paragraphs:
        reasons.append("paragraph_structure_lost")

    if source.strip() and stripped:
        ratio = len(stripped) / len(source.strip())
        if ratio < 0.55:
            reasons.append("massive_deletion")
        if ratio > 2.75:
            reasons.append("massive_expansion")
        if _looks_truncated(source.strip(), stripped):
            reasons.append("truncated_output")

    return {
        "accepted": not reasons,
        "output": stripped,
        "reasons": reasons,
    }


def _clean_control_characters(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "\r\n\t" or ord(character) >= 32
    )


def _detect_names(value: str) -> set[str]:
    names = set(CAPITALIZED_PATTERN.findall(value))
    names.update(ACRONYM_PATTERN.findall(value))
    return names


def _paragraph_count(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 0
    return len(PARAGRAPH_BREAK.split(stripped))


def _looks_truncated(source: str, output: str) -> bool:
    if output.endswith(("...", "…", ",", ":", ";", "-", "—", "–")):
        return True
    if source.endswith((".", "!", "?")) and not output.endswith(
        (".", "!", "?", '"', "'", "”", "’", ")", "]")
    ):
        return True
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    return any(output.count(left) != output.count(right) for left, right in pairs)


def _match_line_endings(value: str, source: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\r\n" in source:
        return normalized.replace("\n", "\r\n")
    if "\r" in source and "\n" not in source:
        return normalized.replace("\n", "\r")
    return normalized


def _separate_outer_whitespace(value: str) -> tuple[str, str, str]:
    leading_match = re.match(r"^\s*", value)
    leading = leading_match.group(0) if leading_match else ""
    without_leading = value[len(leading) :]
    trailing_match = re.search(r"\s*$", without_leading)
    trailing = trailing_match.group(0) if trailing_match else ""
    content = (
        without_leading[: -len(trailing)] if trailing else without_leading
    )
    return leading, content, trailing
