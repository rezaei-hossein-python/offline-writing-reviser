from __future__ import annotations

import re


PARAGRAPH_BOUNDARY_PATTERN = re.compile(
    r"(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))+"
)
SENTENCE_BOUNDARY_PATTERN = re.compile(
    r"(?<=[.!?])(?:[ \t]+|\r\n|\r|\n)"
)
CLAUSE_BOUNDARY_PATTERN = re.compile(r"(?<=[;,:])(?:[ \t]+|\r\n|\r|\n)")
WORD_BOUNDARY_PATTERN = re.compile(r"\s+")


def split_proofreading_chunks(text: str, target_characters: int) -> list[str]:
    """Split text into contiguous, word-safe chunks without changing any bytes."""
    if target_characters < 1:
        raise ValueError("Chunk target must be positive")
    if len(text) <= target_characters:
        return [text]

    paragraph_boundaries = _boundary_positions(text, PARAGRAPH_BOUNDARY_PATTERN)
    sentence_boundaries = _boundary_positions(text, SENTENCE_BOUNDARY_PATTERN)
    clause_boundaries = _boundary_positions(text, CLAUSE_BOUNDARY_PATTERN)
    word_boundaries = _boundary_positions(text, WORD_BOUNDARY_PATTERN)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        limit = min(start + target_characters, len(text))
        paragraph_end = _preferred_paragraph_boundary(
            paragraph_boundaries, start, limit, target_characters
        )
        if paragraph_end is not None and paragraph_end < len(text):
            end = paragraph_end
        elif limit == len(text):
            end = len(text)
        else:
            end = (
                _latest_boundary(sentence_boundaries, start, limit)
                or _latest_boundary(clause_boundaries, start, limit)
                or _latest_boundary(word_boundaries, start, limit)
                or _next_boundary(word_boundaries, limit)
                or len(text)
            )
        if end <= start:
            raise RuntimeError("Chunking failed to make progress")
        chunks.append(text[start:end])
        start = end

    if "".join(chunks) != text:
        raise RuntimeError("Chunking changed the source text")
    return chunks


def _boundary_positions(text: str, pattern: re.Pattern[str]) -> list[int]:
    return [match.end() for match in pattern.finditer(text)]


def _latest_boundary(boundaries: list[int], start: int, limit: int) -> int | None:
    for position in reversed(boundaries):
        if start < position <= limit:
            return position
    return None


def _preferred_paragraph_boundary(
    boundaries: list[int], start: int, limit: int, target: int
) -> int | None:
    """Keep useful paragraphs whole instead of packing unrelated ones together."""
    minimum = min(160, max(1, target // 4))
    candidates = [position for position in boundaries if start < position <= limit]
    for position in candidates:
        if position - start >= minimum:
            return position
    return candidates[-1] if candidates else None


def _next_boundary(boundaries: list[int], limit: int) -> int | None:
    for position in boundaries:
        if position > limit:
            return position
    return None
