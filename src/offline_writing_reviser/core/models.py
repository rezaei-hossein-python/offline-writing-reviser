from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(frozen=True)
class WritingRevisionResult:
    original_character_count: int
    revised_text: str
    provider: str
    model: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
