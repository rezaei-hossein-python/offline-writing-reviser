from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from typing import Any

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import (
    OfflineWritingBusy,
    OfflineWritingInputError,
)
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
from offline_writing_reviser.proofreading.languagetool import (
    LanguageToolRuntime,
)
from offline_writing_reviser.proofreading.policy import (
    apply_deterministic_language_fixes,
    build_gemma_instruction,
    normalize_matches,
    route_post_safe,
    safe_filter,
    validate_gemma_output,
)
from offline_writing_reviser.providers.base import OfflineWritingProviderError
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
)


class HybridProofreadingService:
    """Production LanguageTool SAFE + evidence-routed Gemma service."""

    def __init__(
        self,
        provider: OllamaCliOfflineWritingProvider,
        language_tool: LanguageToolRuntime,
        config: OfflineWritingConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        self.provider = provider
        self.language_tool = language_tool
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
        records: list[dict[str, Any]] = []
        self.logger.info(
            "Hybrid proofreading started chars=%s model=%s",
            len(selected_text),
            self.provider.model_identifier,
        )
        try:
            chunks = split_proofreading_chunks(
                selected_text, self.config.chunk_characters
            )
            revised_chunks: list[str] = []
            for index, chunk in enumerate(chunks, 1):
                prefix, content, suffix = _separate_outer_whitespace(chunk)
                if content:
                    output, record = self._revise_chunk(
                        content, index, len(chunks)
                    )
                    records.append(record)
                else:
                    output = content
                revised_chunks.append(prefix + output + suffix)
            revised_text = sanitize_revision_output(
                "".join(revised_chunks), original_text=selected_text
            )
            duration_ms = (time.perf_counter() - started) * 1000
            metadata = _summary_metadata(records)
            self.logger.info(
                "Hybrid proofreading completed chars=%s revised_chars=%s "
                "duration_ms=%.2f chunks=%s gemma_routed=%s "
                "gemma_accepted=%s gemma_fallback=%s lt_seconds=%.3f "
                "gemma_seconds=%.3f",
                len(selected_text),
                len(revised_text),
                duration_ms,
                len(chunks),
                metadata["gemma_routed"],
                metadata["gemma_accepted"],
                metadata["gemma_fallback"],
                metadata["language_tool_seconds"],
                metadata["gemma_seconds"],
            )
            return WritingRevisionResult(
                original_character_count=len(selected_text),
                revised_text=revised_text,
                provider="languagetool_ollama_hybrid",
                model=self.provider.model_identifier,
                duration_ms=duration_ms,
                metadata=metadata,
            )
        finally:
            self._lock.release()

    def _revise_chunk(
        self, source: str, index: int, chunk_count: int
    ) -> tuple[str, dict[str, Any]]:
        original_payload, original_latency = self.language_tool.check(source)
        original_matches = normalize_matches(original_payload, source)
        safe_output, safe_decisions, filter_latency = safe_filter(
            source, original_matches
        )
        safe_count = sum(
            bool(decision["accepted"]) for decision in safe_decisions
        )
        safe_rule_ids = {
            decision["rule_id"]
            for decision in safe_decisions
            if decision["accepted"]
        }
        safe_output, language_fixes = apply_deterministic_language_fixes(
            safe_output
        )
        post_payload, post_latency = self.language_tool.check(safe_output)
        post_matches = normalize_matches(post_payload, safe_output)
        post_safe_output, post_decisions, post_filter_latency = safe_filter(
            safe_output, post_matches
        )
        if post_safe_output != safe_output:
            safe_output = post_safe_output
            safe_count += sum(
                bool(decision["accepted"]) for decision in post_decisions
            )
            safe_rule_ids.update(
                decision["rule_id"]
                for decision in post_decisions
                if decision["accepted"]
            )
            final_payload, final_latency = self.language_tool.check(safe_output)
            post_latency += final_latency
            post_matches = normalize_matches(final_payload, safe_output)
            _, post_decisions, final_filter_latency = safe_filter(
                safe_output, post_matches
            )
            post_filter_latency += final_filter_latency
        routing = route_post_safe(
            post_matches,
            post_decisions,
            safe_count + len(language_fixes),
            safe_output,
        )
        record: dict[str, Any] = {
            "chunk_index": index,
            "chunk_count": chunk_count,
            "character_count": len(source),
            "safe_correction_count": safe_count,
            "safe_rule_ids": sorted(safe_rule_ids),
            "deterministic_language_fixes": language_fixes,
            "routing_decision": routing["route_to_gemma"],
            "routing_reason": routing["reason"],
            "routing_rule_ids": sorted(
                {item["rule_id"] for item in routing["evidence"]}
            ),
            "quality_signals": routing["quality_signals"],
            "language_tool_seconds": original_latency + post_latency,
            "safe_filter_seconds": filter_latency + post_filter_latency,
            "gemma_seconds": 0.0,
            "gemma_accepted": False,
            "gemma_fallback": False,
            "gemma_rejection_reasons": [],
            "ollama_telemetry": {},
            "acceleration": "unknown",
        }
        if not routing["route_to_gemma"]:
            self._log_chunk(record)
            return safe_output, record

        instruction = build_gemma_instruction(
            routing["evidence"], routing["quality_signals"]
        )
        gemma_started = time.perf_counter()
        try:
            inference = self.provider.revise_with_telemetry(
                safe_output,
                instruction,
                timeout_seconds=self.config.timeout_seconds,
            )
            record["ollama_telemetry"] = inference.telemetry
            validation = validate_gemma_output(
                safe_output,
                inference.text,
                routing["evidence"],
                routing["quality_signals"],
            )
            if validation["accepted"]:
                candidate_payload, candidate_latency = (
                    self.language_tool.check(inference.text)
                )
                record["language_tool_seconds"] += candidate_latency
                candidate_matches = normalize_matches(
                    candidate_payload, inference.text
                )
                _, candidate_decisions, candidate_filter_latency = safe_filter(
                    inference.text, candidate_matches
                )
                record["safe_filter_seconds"] += candidate_filter_latency
                candidate_routing = route_post_safe(
                    candidate_matches,
                    candidate_decisions,
                    0,
                    inference.text,
                )
                introduced_safe_error = any(
                    decision["policy_group"] == "SAFE"
                    and decision["accepted"]
                    for decision in candidate_decisions
                )
                source_burden = _quality_burden(
                    routing["evidence"], routing["quality_signals"]
                )
                candidate_burden = _quality_burden(
                    candidate_routing["evidence"],
                    candidate_routing["quality_signals"],
                )
                record["source_quality_burden"] = source_burden
                record["candidate_quality_burden"] = candidate_burden
                if introduced_safe_error:
                    output = safe_output
                    record["gemma_fallback"] = True
                    record["gemma_rejection_reasons"] = [
                        "introduced_deterministic_error"
                    ]
                elif (
                    inference.text != safe_output
                    and candidate_burden >= source_burden
                ):
                    output = safe_output
                    record["gemma_fallback"] = True
                    record["gemma_rejection_reasons"] = [
                        "no_demonstrable_quality_improvement"
                    ]
                else:
                    output = inference.text
                    record["gemma_accepted"] = True
            else:
                output = safe_output
                record["gemma_fallback"] = True
                record["gemma_rejection_reasons"] = validation[
                    "rejection_reasons"
                ]
        except OfflineWritingProviderError as exc:
            output = safe_output
            record["gemma_fallback"] = True
            record["gemma_rejection_reasons"] = [exc.__class__.__name__]
            self.logger.warning(
                "Hybrid Gemma fallback chunk_index=%s category=%s",
                index,
                exc.__class__.__name__,
            )
        record["gemma_seconds"] = time.perf_counter() - gemma_started
        try:
            runtime = self.provider.runtime_diagnostics(timeout_seconds=2.0)
            record["acceleration"] = runtime["acceleration"]
        except OfflineWritingProviderError:
            pass
        self._log_chunk(record)
        return output, record

    def _log_chunk(self, record: dict[str, Any]) -> None:
        telemetry = record["ollama_telemetry"]
        self.logger.info(
            "Hybrid chunk completed chunk_index=%s chunk_count=%s chars=%s "
            "safe_corrections=%s safe_rules=%s language_fixes=%s routed=%s "
            "routing_reason=%s rules=%s "
            "lt_seconds=%.3f gemma_seconds=%.3f accepted=%s fallback=%s "
            "rejection_reasons=%s acceleration=%s load_seconds=%s "
            "prompt_eval_seconds=%s generation_seconds=%s "
            "prompt_tokens=%s generation_tokens=%s",
            record["chunk_index"],
            record["chunk_count"],
            record["character_count"],
            record["safe_correction_count"],
            ",".join(record["safe_rule_ids"]) or "none",
            ",".join(record["deterministic_language_fixes"]) or "none",
            record["routing_decision"],
            record["routing_reason"],
            ",".join(record["routing_rule_ids"]) or "none",
            record["language_tool_seconds"],
            record["gemma_seconds"],
            record["gemma_accepted"],
            record["gemma_fallback"],
            ",".join(record["gemma_rejection_reasons"]) or "none",
            record["acceleration"],
            telemetry.get("load_duration_seconds"),
            telemetry.get("prompt_eval_duration_seconds"),
            telemetry.get("generation_duration_seconds"),
            telemetry.get("prompt_token_count"),
            telemetry.get("generation_token_count"),
        )


def _summary_metadata(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "chunk_count": len(records),
        "safe_correction_count": sum(
            record["safe_correction_count"] for record in records
        ),
        "safe_rule_ids": sorted(
            {
                rule_id
                for record in records
                for rule_id in record["safe_rule_ids"]
            }
        ),
        "gemma_routed": sum(
            bool(record["routing_decision"]) for record in records
        ),
        "gemma_accepted": sum(
            bool(record["gemma_accepted"]) for record in records
        ),
        "gemma_fallback": sum(
            bool(record["gemma_fallback"]) for record in records
        ),
        "deterministic_language_correction_count": sum(
            len(record["deterministic_language_fixes"])
            for record in records
        ),
        "deterministic_language_fixes": sorted(
            {
                fix
                for record in records
                for fix in record["deterministic_language_fixes"]
            }
        ),
        "gemma_rejection_reasons": sorted(
            {
                reason
                for record in records
                for reason in record["gemma_rejection_reasons"]
            }
        ),
        "language_tool_seconds": sum(
            record["language_tool_seconds"] for record in records
        ),
        "gemma_seconds": sum(record["gemma_seconds"] for record in records),
            "routing_reasons": dict(
            Counter(record["routing_reason"] for record in records)
        ),
        "acceleration": next(
            (
                record["acceleration"]
                for record in reversed(records)
                if record["acceleration"] != "unknown"
            ),
            "unknown",
        ),
    }


def _quality_burden(
    evidence: list[dict[str, Any]], quality_signals: list[str]
) -> int:
    return len(evidence) * 2 + len(quality_signals)


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
