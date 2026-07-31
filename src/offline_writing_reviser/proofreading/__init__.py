"""Deterministic semantic safeguards for intelligent revision."""

from offline_writing_reviser.proofreading.semantic import (
    SemanticValidation,
    meaning_anchor_preserved,
    protected_values,
    validate_semantic_preservation,
)

__all__ = [
    "SemanticValidation",
    "meaning_anchor_preserved",
    "protected_values",
    "validate_semantic_preservation",
]
