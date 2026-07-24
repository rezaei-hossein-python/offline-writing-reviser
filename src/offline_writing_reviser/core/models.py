from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WritingRevisionResult:
    original_character_count: int
    revised_text: str
    provider: str
    model: str
    duration_ms: float
