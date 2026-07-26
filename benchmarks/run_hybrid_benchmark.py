#!/usr/bin/env python3
"""Benchmark-only LanguageTool SAFE + Gemma hybrid proofreading pipeline."""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import re
import statistics
import time
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_languagetool_benchmark import (
    AMBIGUOUS,
    IGNORE,
    RULE_POLICY,
    SAFE,
    DEFAULT_JAVA,
    DEFAULT_SERVER_JAR,
    LanguageToolClient,
    LanguageToolServer,
    apply_edits,
    find_expected_edit_path,
    formatting_signature,
    load_cases,
    normalize_matches,
    percentile,
    safe_filter,
    safe_rate,
    validate_bundled_runtime,
)
from run_proofreading_benchmark import DEFAULT_OPTIONS, OllamaClient


MODEL = "gemma3:4b"
PROMPT_VERSION = "phase18d-v1"
DEFAULT_GEMMA_KEEP_ALIVE = "10m"
HYBRID_PROMPT = """You are the second stage of a conservative proofreading system.

Correct only genuine objective grammar or spelling errors in the supplied text.
Make the smallest possible changes.

Do not improve style, paraphrase, change tone, substitute optional vocabulary, or rewrite a sentence unless a short grammatical correction requires it.
Preserve meaning, facts, capitalization, punctuation, spacing, typography, paragraphs, line breaks, blank lines, list markers, and formatting.
The LanguageTool evidence below is advisory. Correct only errors that are genuinely supported by the text and context.
If no correction is needed, return the supplied text exactly unchanged.

Return only the resulting text. Do not add explanations, headings, commentary, markdown wrappers, quotation marks, labels, or reasoning."""

CONTEXTUAL_SAFE_REJECTION_REASONS = {
    "multiple_replacements_without_approved_choice",
    "no_actionable_replacement",
    "replacement_not_single_word",
    "replacement_changes_case_pattern",
    "non_word_source",
}
AMBIGUOUS_NON_GRAMMAR_RULES = {"CALENDER"}
COMMENTARY_PREFIX = re.compile(
    r"^\s*(?:here(?:'s| is)|corrected(?: text)?|revised(?: text)?|"
    r"revision|output|result)\s*[:\-]",
    re.IGNORECASE,
)
NEWLINE_PATTERN = re.compile(r"\r\n|\r|\n")


class ResidentOllamaClient(OllamaClient):
    """Phase 18E benchmark client with explicit residency and telemetry."""

    def __init__(self, base_url: str, timeout: float, keep_alive: str):
        super().__init__(base_url, timeout)
        self.keep_alive = keep_alive
        self.cold_start_pending = False

    def unload(self, model: str) -> None:
        self.request(
            "/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        )
        self.cold_start_pending = True

    def generate(
        self, model: str, text: str, instruction: str = HYBRID_PROMPT
    ) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            # Preserve the Phase 18D inference settings exactly. The Phase 18E
            # harness found that smaller limits did not improve steady latency.
            "options": DEFAULT_OPTIONS,
        }
        payload_bytes = len(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        result = self.request("/api/chat", payload)
        response_bytes = len(
            json.dumps(result, ensure_ascii=False).encode("utf-8")
        )
        result["_benchmark_request_payload_bytes"] = payload_bytes
        result["_benchmark_response_bytes"] = response_bytes
        result["_benchmark_cold_start"] = self.cold_start_pending
        self.cold_start_pending = False
        return str(result.get("message", {}).get("content", "")), result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark LanguageTool SAFE corrections followed by evidence-routed "
            "Gemma proofreading and conservative output validation."
        )
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("proofreading_cases.json"),
        help="Benchmark case JSON path.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(__file__).with_name("results") / "hybrid",
        help="Generated output directory (gitignored by default).",
    )
    parser.add_argument("--java", type=Path, default=DEFAULT_JAVA)
    parser.add_argument("--server-jar", type=Path, default=DEFAULT_SERVER_JAR)
    parser.add_argument("--lt-host", default="127.0.0.1")
    parser.add_argument("--lt-port", type=int, default=8081)
    parser.add_argument("--lt-startup-timeout", type=float, default=60.0)
    parser.add_argument("--lt-request-timeout", type=float, default=30.0)
    parser.add_argument(
        "--ollama-base-url",
        default="http://127.0.0.1:11434",
        help="Local Ollama API base URL.",
    )
    parser.add_argument("--model", default=MODEL, help="Installed local Gemma model.")
    parser.add_argument("--gemma-timeout", type=float, default=180.0)
    parser.add_argument(
        "--gemma-keep-alive",
        default=DEFAULT_GEMMA_KEEP_ALIVE,
        help="Ollama model residency after a routed request (default: %(default)s).",
    )
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help="Explicitly unload Gemma before the run to measure one cold request.",
    )
    parser.add_argument(
        "--gemma-only-results",
        type=Path,
        default=Path(__file__).with_name("results") / "latest.json",
        help="Existing model benchmark JSON used only for optional comparison.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N cases (smoke testing only).",
    )
    return parser.parse_args()


def compact_evidence(
    matches: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    decision_by_index = {
        decision["match_index"]: decision for decision in decisions
    }
    evidence: list[dict[str, Any]] = []
    for match_item in matches:
        decision = decision_by_index[match_item["match_index"]]
        policy_group = decision["policy_group"]
        escalation_class: str | None = None
        if (
            policy_group == AMBIGUOUS
            and match_item["rule_id"] not in AMBIGUOUS_NON_GRAMMAR_RULES
        ):
            escalation_class = "ambiguous_grammar"
        elif (
            policy_group == SAFE
            and not decision["accepted"]
            and decision["reason"] in CONTEXTUAL_SAFE_REJECTION_REASONS
        ):
            escalation_class = "contextual_spelling"
        if escalation_class is None:
            continue
        evidence.append(
            {
                "match_index": match_item["match_index"],
                "escalation_class": escalation_class,
                "rule_id": match_item["rule_id"],
                "category_id": match_item["category_id"],
                "message": match_item["message"],
                "short_message": match_item["short_message"],
                "offset": match_item["offset"],
                "length": match_item["length"],
                "original_text": match_item["original_text"],
                "replacement_candidates": match_item["replacements"][:8],
                "safe_rejection_reason": decision["reason"],
            }
        )
    return evidence


def route_post_safe(
    post_matches: list[dict[str, Any]],
    post_decisions: list[dict[str, Any]],
    safe_correction_count: int,
) -> dict[str, Any]:
    evidence = compact_evidence(post_matches, post_decisions)
    if evidence:
        classes = {item["escalation_class"] for item in evidence}
        if safe_correction_count:
            reason = (
                "safe_partial_with_unresolved_grammar"
                if "ambiguous_grammar" in classes
                else "safe_partial_with_contextual_spelling"
            )
        elif "ambiguous_grammar" in classes:
            reason = "remaining_ambiguous_grammar"
        else:
            reason = "remaining_contextual_spelling"
        return {"route_to_gemma": True, "reason": reason, "evidence": evidence}

    if not post_matches:
        reason = (
            "safe_resolved_all_actionable_evidence"
            if safe_correction_count
            else "clean_no_meaningful_evidence"
        )
        return {"route_to_gemma": False, "reason": reason, "evidence": []}

    policy_groups = {
        decision["policy_group"] for decision in post_decisions
    }
    if policy_groups and policy_groups <= {IGNORE}:
        reason = "ignore_only_evidence"
    elif any(
        decision["policy_group"] == SAFE and decision["accepted"]
        for decision in post_decisions
    ):
        reason = "post_safe_deterministic_evidence_not_escalated"
    else:
        reason = "non_escalatable_evidence"
    return {"route_to_gemma": False, "reason": reason, "evidence": []}


def build_gemma_instruction(evidence: list[dict[str, Any]]) -> str:
    lines = [HYBRID_PROMPT, "", "Unresolved LanguageTool evidence:"]
    for index, item in enumerate(evidence, 1):
        replacements = ", ".join(
            repr(value) for value in item["replacement_candidates"]
        )
        lines.append(
            f"{index}. rule={item['rule_id']} category={item['category_id']} "
            f"span={item['offset']}:{item['offset'] + item['length']} "
            f"text={item['original_text']!r} message={item['message']!r} "
            f"candidates=[{replacements}]"
        )
    return "\n".join(lines)


def prompt_metadata(
    model: str, evidence: list[dict[str, Any]], instruction: str
) -> dict[str, Any]:
    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        "unresolved_rule_ids": sorted(
            {item["rule_id"] for item in evidence}
        ),
        "evidence": evidence,
    }


def newline_tokens(value: str) -> list[str]:
    return NEWLINE_PATTERN.findall(value)


def changed_opcodes(source: str, output: str) -> list[dict[str, Any]]:
    matcher = difflib.SequenceMatcher(a=source, b=output, autojunk=False)
    return [
        {
            "tag": tag,
            "source_start": source_start,
            "source_end": source_end,
            "output_start": output_start,
            "output_end": output_end,
            "source_text": source[source_start:source_end],
            "output_text": output[output_start:output_end],
        }
        for tag, source_start, source_end, output_start, output_end in matcher.get_opcodes()
        if tag != "equal"
    ]


def evidence_windows(
    source: str, evidence: list[dict[str, Any]], radius: int = 40
) -> list[dict[str, int]]:
    windows: list[dict[str, int]] = []
    for item in evidence:
        offset = item["offset"]
        match_end = offset + item["length"]
        previous_boundaries = [
            source.rfind(marker, 0, offset)
            for marker in ("\n", ". ", "? ", "! ")
        ]
        previous = max(previous_boundaries)
        sentence_start = 0 if previous < 0 else previous + (1 if source[previous] == "\n" else 2)
        following = [
            position
            for position in (
                source.find("\n", match_end),
                source.find(". ", match_end),
                source.find("? ", match_end),
                source.find("! ", match_end),
            )
            if position >= 0
        ]
        sentence_end = min(following) + 1 if following else len(source)
        windows.append(
            {
                "start": max(sentence_start, offset - radius),
                "end": min(sentence_end, match_end + radius),
            }
        )
    return windows


def opcode_is_local(
    opcode: dict[str, Any], windows: list[dict[str, int]]
) -> bool:
    start = opcode["source_start"]
    end = opcode["source_end"]
    if start == end:
        return any(window["start"] <= start <= window["end"] for window in windows)
    return any(
        window["start"] <= start and end <= window["end"]
        for window in windows
    )


def validate_gemma_output(
    source: str,
    output: str | None,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    reasons: list[str] = []
    if output is None:
        reasons.append("missing_output")
        return {
            "accepted": False,
            "rejection_reasons": reasons,
            "changed_opcodes": [],
            "changed_character_budget": 0,
            "evidence_windows": evidence_windows(source, evidence),
            "latency_seconds": time.perf_counter() - started,
        }
    if not output or not output.strip():
        reasons.append("empty_output")
    if "\x00" in output:
        reasons.append("null_byte")
    if "```" not in source and "```" in output:
        reasons.append("markdown_wrapper")
    if not source.lstrip().startswith("# ") and output.lstrip().startswith("# "):
        reasons.append("markdown_wrapper")
    if COMMENTARY_PREFIX.match(output) and not COMMENTARY_PREFIX.match(source):
        reasons.append("commentary_or_label")
    if newline_tokens(source) != newline_tokens(output):
        reasons.append("newline_structure_changed")
    if formatting_signature(source) != formatting_signature(output):
        reasons.append("formatting_structure_changed")

    source_length = len(source)
    output_length = len(output)
    if source_length >= 20 and output_length < source_length * 0.65:
        reasons.append("possible_truncation")
    if abs(output_length - source_length) > max(20, math.ceil(source_length * 0.25)):
        reasons.append("extreme_length_change")

    opcodes = changed_opcodes(source, output)
    changed_budget = sum(
        max(
            opcode["source_end"] - opcode["source_start"],
            opcode["output_end"] - opcode["output_start"],
        )
        for opcode in opcodes
    )
    maximum_changed = max(16, math.ceil(source_length * 0.25), len(evidence) * 12)
    if changed_budget > maximum_changed:
        reasons.append("excessive_character_changes")
    if len(opcodes) > max(4, len(evidence) * 3):
        reasons.append("excessive_edit_segments")

    windows = evidence_windows(source, evidence)
    if opcodes and not windows:
        reasons.append("edit_without_unresolved_evidence")
    elif any(not opcode_is_local(opcode, windows) for opcode in opcodes):
        reasons.append("edit_outside_evidence_window")

    for item in evidence:
        candidates = item["replacement_candidates"]
        if (
            item["escalation_class"] != "contextual_spelling"
            or len(candidates) < 2
        ):
            continue
        candidate_only_outputs = {
            apply_edits(
                source,
                [
                    {
                        "offset": item["offset"],
                        "length": item["length"],
                        "replacement": candidate,
                    }
                ],
            )
            for candidate in candidates
        }
        if output in candidate_only_outputs:
            reasons.append(
                "ambiguous_spelling_candidate_without_contextual_resolution"
            )

    return {
        "accepted": not reasons,
        "rejection_reasons": list(dict.fromkeys(reasons)),
        "changed_opcodes": opcodes,
        "changed_character_budget": changed_budget,
        "maximum_changed_character_budget": maximum_changed,
        "evidence_windows": windows,
        "latency_seconds": time.perf_counter() - started,
    }


def invoke_gemma(
    client: OllamaClient,
    model: str,
    text: str,
    instruction: str,
) -> tuple[str | None, dict[str, Any] | None, float, str | None]:
    started = time.perf_counter()
    try:
        output, response = client.generate(model, text, instruction)
        return output, response, time.perf_counter() - started, None
    except (
        OSError,
        TimeoutError,
        UnicodeError,
        ValueError,
        KeyError,
        urllib.error.URLError,
    ) as exc:
        return (
            None,
            None,
            time.perf_counter() - started,
            f"{type(exc).__name__}: {exc}",
        )


def hybrid_case(
    case: dict[str, Any],
    lt_client: LanguageToolClient,
    gemma_client: OllamaClient,
    model: str,
) -> dict[str, Any]:
    case_started = time.perf_counter()
    original_payload, original_lt_latency = lt_client.check(case["input"])
    original_matches = normalize_matches(original_payload, case["input"])
    raw_path = find_expected_edit_path(
        case["input"], case["expected"], original_matches
    )

    safe_output, safe_decisions, safe_filter_latency = safe_filter(
        case["input"], original_matches
    )
    safe_correction_count = sum(
        decision["accepted"] for decision in safe_decisions
    )
    post_payload, post_lt_latency = lt_client.check(safe_output)
    post_matches = normalize_matches(post_payload, safe_output)
    _, post_decisions, post_filter_latency = safe_filter(
        safe_output, post_matches
    )
    routing = route_post_safe(
        post_matches, post_decisions, safe_correction_count
    )

    instruction = None
    metadata = None
    raw_output = None
    gemma_response = None
    gemma_latency = 0.0
    gemma_error = None
    validation: dict[str, Any] | None = None
    if routing["route_to_gemma"]:
        instruction = build_gemma_instruction(routing["evidence"])
        metadata = prompt_metadata(model, routing["evidence"], instruction)
        raw_output, gemma_response, gemma_latency, gemma_error = invoke_gemma(
            gemma_client, model, safe_output, instruction
        )
        if gemma_error:
            validation = {
                "accepted": False,
                "rejection_reasons": ["gemma_provider_error"],
                "changed_opcodes": [],
                "changed_character_budget": 0,
                "evidence_windows": evidence_windows(
                    safe_output, routing["evidence"]
                ),
                "latency_seconds": 0.0,
            }
        else:
            validation = validate_gemma_output(
                safe_output, raw_output, routing["evidence"]
            )

    accepted = bool(validation and validation["accepted"])
    final_output = raw_output if accepted and raw_output is not None else safe_output
    expected_change = case["input"] != case["expected"]
    safe_exact = safe_output == case["expected"]
    final_exact = final_output == case["expected"]
    response_metrics = gemma_response or {}
    eval_count = response_metrics.get("eval_count")
    eval_duration = response_metrics.get("eval_duration")
    total_latency = time.perf_counter() - case_started
    return {
        "case_id": case["id"],
        "category": case["category"],
        "original": case["input"],
        "expected": case["expected"],
        "expected_change": expected_change,
        "original_lt_raw_response": original_payload,
        "original_lt_matches": original_matches,
        "raw_expected_output_reachable": raw_path is not None,
        "raw_supporting_edits": raw_path or [],
        "safe_output": safe_output,
        "safe_output_changed": safe_output != case["input"],
        "safe_exact_match": safe_exact,
        "safe_corrections": [
            decision for decision in safe_decisions if decision["accepted"]
        ],
        "safe_decisions": safe_decisions,
        "post_safe_lt_raw_response": post_payload,
        "post_safe_lt_matches": post_matches,
        "post_safe_decisions": post_decisions,
        "routing_decision": routing["route_to_gemma"],
        "routing_reason": routing["reason"],
        "routing_evidence": routing["evidence"],
        "gemma_prompt_metadata": metadata,
        "raw_gemma_output": raw_output,
        "gemma_provider_error": gemma_error,
        "gemma_response_metrics": {
            "cold_start": response_metrics.get("_benchmark_cold_start"),
            "request_payload_bytes": response_metrics.get(
                "_benchmark_request_payload_bytes"
            ),
            "response_bytes": response_metrics.get(
                "_benchmark_response_bytes"
            ),
            "total_duration_ns": response_metrics.get("total_duration"),
            "load_duration_ns": response_metrics.get("load_duration"),
            "prompt_eval_count": response_metrics.get("prompt_eval_count"),
            "prompt_eval_duration_ns": response_metrics.get(
                "prompt_eval_duration"
            ),
            "eval_count": eval_count,
            "eval_duration_ns": eval_duration,
            "tokens_per_second": (
                eval_count / (eval_duration / 1_000_000_000)
                if eval_count and eval_duration
                else None
            ),
        },
        "gemma_latency_seconds": gemma_latency,
        "gemma_validation": validation,
        "gemma_output_accepted": accepted,
        "final_output": final_output,
        "final_exact_match": final_exact,
        "final_exact_preservation": (
            final_output == case["input"] if not expected_change else None
        ),
        "final_unnecessary_edit": (
            final_output != case["input"] if not expected_change else False
        ),
        "final_formatting_preservation": (
            formatting_signature(final_output)
            == formatting_signature(case["expected"])
            if case["category"] == "formatting"
            else None
        ),
        "improved_by_gemma": (
            routing["route_to_gemma"] and not safe_exact and final_exact
        ),
        "made_worse_by_gemma": (
            routing["route_to_gemma"] and safe_exact and not final_exact
        ),
        "latency": {
            "original_lt_seconds": original_lt_latency,
            "safe_filter_seconds": safe_filter_latency,
            "post_safe_lt_seconds": post_lt_latency,
            "post_safe_filter_seconds": post_filter_latency,
            "gemma_seconds": gemma_latency,
            "validation_seconds": (
                validation["latency_seconds"] if validation else 0.0
            ),
            "total_seconds": total_latency,
        },
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    correct = [
        record for record in records if record["category"] == "already_correct"
    ]
    unchanged = [record for record in records if not record["expected_change"]]
    corrections = [record for record in records if record["expected_change"]]
    formatting = [
        record for record in records if record["category"] == "formatting"
    ]
    routed = [record for record in records if record["routing_decision"]]
    accepted = [record for record in routed if record["gemma_output_accepted"]]
    rejected = [record for record in routed if not record["gemma_output_accepted"]]
    latencies = [record["latency"]["total_seconds"] for record in records]
    gemma_latencies = [
        record["gemma_latency_seconds"] for record in routed
    ]
    cold_routed = [
        record
        for record in routed
        if record["gemma_response_metrics"].get("cold_start") is True
    ]
    warm_routed = [
        record
        for record in routed
        if record["gemma_response_metrics"].get("cold_start") is not True
    ]
    warm_gemma_latencies = [
        record["gemma_latency_seconds"] for record in warm_routed
    ]
    def warm_metric(name: str) -> float | None:
        values = [
            record["gemma_response_metrics"].get(name)
            for record in warm_routed
        ]
        numeric = [float(value) for value in values if value is not None]
        return statistics.fmean(numeric) if numeric else None

    warm_load_ns = warm_metric("load_duration_ns")
    warm_prompt_eval_ns = warm_metric("prompt_eval_duration_ns")
    warm_eval_ns = warm_metric("eval_duration_ns")
    routing_reasons = Counter(record["routing_reason"] for record in records)
    rejection_reasons: Counter[str] = Counter()
    for record in rejected:
        validation = record["gemma_validation"] or {}
        rejection_reasons.update(validation.get("rejection_reasons", []))

    raw_accuracy = safe_rate(
        sum(record["raw_expected_output_reachable"] for record in corrections),
        len(corrections),
    )
    safe_accuracy = safe_rate(
        sum(record["safe_exact_match"] for record in corrections),
        len(corrections),
    )
    final_accuracy = safe_rate(
        sum(record["final_exact_match"] for record in corrections),
        len(corrections),
    )
    return {
        "case_count": len(records),
        "raw_lt_reachability": raw_accuracy,
        "safe_exact_correction_accuracy": safe_accuracy,
        "hybrid_exact_correction_accuracy": final_accuracy,
        "hybrid_exact_preservation_rate": safe_rate(
            sum(record["final_exact_preservation"] is True for record in correct),
            len(correct),
        ),
        "hybrid_over_edit_rate": safe_rate(
            sum(record["final_unnecessary_edit"] for record in unchanged),
            len(unchanged),
        ),
        "hybrid_formatting_preservation_rate": safe_rate(
            sum(
                record["final_formatting_preservation"] is True
                for record in formatting
            ),
            len(formatting),
        ),
        "mean_latency_seconds": statistics.fmean(latencies) if latencies else None,
        "median_latency_seconds": statistics.median(latencies) if latencies else None,
        "p95_latency_seconds": percentile(latencies, 0.95),
        "mean_gemma_latency_seconds": (
            statistics.fmean(gemma_latencies) if gemma_latencies else None
        ),
        "median_gemma_latency_seconds": (
            statistics.median(gemma_latencies) if gemma_latencies else None
        ),
        "p95_gemma_latency_seconds": percentile(gemma_latencies, 0.95),
        "cold_gemma_request_count": len(cold_routed),
        "cold_gemma_latency_seconds": (
            cold_routed[0]["gemma_latency_seconds"] if cold_routed else None
        ),
        "warm_gemma_request_count": len(warm_routed),
        "mean_warm_gemma_latency_seconds": (
            statistics.fmean(warm_gemma_latencies)
            if warm_gemma_latencies
            else None
        ),
        "median_warm_gemma_latency_seconds": (
            statistics.median(warm_gemma_latencies)
            if warm_gemma_latencies
            else None
        ),
        "p95_warm_gemma_latency_seconds": (
            percentile(warm_gemma_latencies, 0.95)
            if warm_gemma_latencies
            else None
        ),
        "mean_warm_load_duration_seconds": (
            warm_load_ns / 1_000_000_000
            if warm_load_ns is not None
            else None
        ),
        "mean_warm_prompt_eval_duration_seconds": (
            warm_prompt_eval_ns / 1_000_000_000
            if warm_prompt_eval_ns is not None
            else None
        ),
        "mean_warm_generation_duration_seconds": (
            warm_eval_ns / 1_000_000_000
            if warm_eval_ns is not None
            else None
        ),
        "mean_warm_prompt_tokens": warm_metric("prompt_eval_count"),
        "mean_warm_generated_tokens": warm_metric("eval_count"),
        "mean_request_payload_bytes": warm_metric("request_payload_bytes"),
        "mean_response_bytes": warm_metric("response_bytes"),
        "gemma_invocation_count": len(routed),
        "gemma_invocation_rate": safe_rate(len(routed), len(records)),
        "gemma_acceptance_count": len(accepted),
        "gemma_rejection_count": len(rejected),
        "cases_resolved_by_languagetool_alone": sum(
            record["expected_change"]
            and record["safe_exact_match"]
            and not record["routing_decision"]
            for record in records
        ),
        "cases_escalated_to_gemma": len(routed),
        "cases_improved_by_gemma": sum(
            record["improved_by_gemma"] for record in records
        ),
        "cases_made_worse_by_gemma": sum(
            record["made_worse_by_gemma"] for record in records
        ),
        "improved_case_ids": [
            record["case_id"] for record in records if record["improved_by_gemma"]
        ],
        "regressed_case_ids": [
            record["case_id"] for record in records if record["made_worse_by_gemma"]
        ],
        "rejected_gemma_case_ids": [
            record["case_id"] for record in rejected
        ],
        "routing_reasons": dict(routing_reasons.most_common()),
        "validation_rejection_reasons": dict(
            rejection_reasons.most_common()
        ),
        "gemma_calls_total": len(routed),
        "gemma_calls_expected_correction": sum(
            record["routing_decision"] for record in corrections
        ),
        "gemma_calls_expected_unchanged": sum(
            record["routing_decision"] for record in unchanged
        ),
        "gemma_calls_already_correct": sum(
            record["routing_decision"] for record in correct
        ),
        "gemma_calls_per_105": safe_rate(len(routed), len(records)),
        "gemma_calls_per_64_corrections": safe_rate(
            sum(record["routing_decision"] for record in corrections),
            len(corrections),
        ),
        "gemma_calls_per_41_unchanged": safe_rate(
            sum(record["routing_decision"] for record in unchanged),
            len(unchanged),
        ),
        "gemma_calls_per_35_correct": safe_rate(
            sum(record["routing_decision"] for record in correct),
            len(correct),
        ),
    }


def load_existing_gemma_baseline(path: Path, model: str) -> dict[str, Any]:
    if not path.is_file():
        return {
            "available": False,
            "reason": f"Benchmark results not found: {path}",
        }
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    summary = next(
        (
            item
            for item in result.get("summary", [])
            if item.get("model") == model
        ),
        None,
    )
    if summary is None:
        return {
            "available": False,
            "reason": f"No {model} summary exists in {path}.",
        }
    return {"available": True, "source": str(path), "summary": summary}


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def markdown_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    baseline = result["comparison"]["gemma_only"]
    lines = [
        "# LanguageTool + Gemma Hybrid Routing Benchmark",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "This pipeline is benchmark-only. Production behavior is unchanged.",
        "",
        "## Comparison",
        "",
        "| Mode | Exact correction | Preservation | Over-edit | Formatting |",
        "|---|---:|---:|---:|---:|",
        f"| Raw LanguageTool reachability | "
        f"{pct(summary['raw_lt_reachability'])} | N/A | N/A | N/A |",
        f"| LanguageTool SAFE actual | "
        f"{pct(summary['safe_exact_correction_accuracy'])} | 100.0% | 0.0% | 100.0% |",
        f"| Hybrid actual | "
        f"{pct(summary['hybrid_exact_correction_accuracy'])} | "
        f"{pct(summary['hybrid_exact_preservation_rate'])} | "
        f"{pct(summary['hybrid_over_edit_rate'])} | "
        f"{pct(summary['hybrid_formatting_preservation_rate'])} |",
        "",
    ]
    if baseline["available"]:
        item = baseline["summary"]
        lines.append(
            f"Existing Gemma-only baseline: {pct(item['exact_correction_accuracy'])} "
            f"correction, {pct(item['exact_preservation_rate'])} preservation, "
            f"{pct(item['over_edit_rate'])} over-edit."
        )
    else:
        lines.append(f"Existing Gemma-only baseline unavailable: {baseline['reason']}")
    lines.extend(
        [
            "",
            "## Routing and validation",
            "",
            f"- Gemma calls: {summary['gemma_invocation_count']}/"
            f"{summary['case_count']} ({pct(summary['gemma_invocation_rate'])})",
            f"- Accepted outputs: {summary['gemma_acceptance_count']}",
            f"- Rejected outputs: {summary['gemma_rejection_count']}",
            f"- LanguageTool-only exact resolutions: "
            f"{summary['cases_resolved_by_languagetool_alone']}",
            f"- Improved by Gemma: {summary['cases_improved_by_gemma']} "
            f"({', '.join(summary['improved_case_ids']) or 'none'})",
            f"- Made worse by Gemma: {summary['cases_made_worse_by_gemma']} "
            f"({', '.join(summary['regressed_case_ids']) or 'none'})",
            f"- Calls on already-correct cases: "
            f"{summary['gemma_calls_already_correct']}/35",
            f"- Calls on expected-unchanged cases: "
            f"{summary['gemma_calls_expected_unchanged']}/41",
            f"- Calls on expected-correction cases: "
            f"{summary['gemma_calls_expected_correction']}/64",
            "",
            "Routing reasons: "
            + ", ".join(
                f"`{reason}`={count}"
                for reason, count in summary["routing_reasons"].items()
            ),
            "",
            "Validation rejection reasons: "
            + (
                ", ".join(
                    f"`{reason}`={count}"
                    for reason, count in summary[
                        "validation_rejection_reasons"
                    ].items()
                )
                or "none"
            ),
            "",
            "## Latency",
            "",
            f"- Total mean/median/P95: {num(summary['mean_latency_seconds'])} / "
            f"{num(summary['median_latency_seconds'])} / "
            f"{num(summary['p95_latency_seconds'])} seconds",
            f"- Routed Gemma mean/median/P95: "
            f"{num(summary['mean_gemma_latency_seconds'])} / "
            f"{num(summary['median_gemma_latency_seconds'])} / "
            f"{num(summary['p95_gemma_latency_seconds'])} seconds",
            f"- Explicit cold Gemma request: "
            f"{num(summary['cold_gemma_latency_seconds'])} seconds "
            f"({summary['cold_gemma_request_count']} request)",
            f"- Warm Gemma mean/median/P95: "
            f"{num(summary['mean_warm_gemma_latency_seconds'])} / "
            f"{num(summary['median_warm_gemma_latency_seconds'])} / "
            f"{num(summary['p95_warm_gemma_latency_seconds'])} seconds "
            f"({summary['warm_gemma_request_count']} requests)",
            f"- Warm mean load/prompt/generation: "
            f"{num(summary['mean_warm_load_duration_seconds'])} / "
            f"{num(summary['mean_warm_prompt_eval_duration_seconds'])} / "
            f"{num(summary['mean_warm_generation_duration_seconds'])} seconds",
            f"- Warm mean prompt/generated tokens: "
            f"{num(summary['mean_warm_prompt_tokens'])} / "
            f"{num(summary['mean_warm_generated_tokens'])}",
            "",
            "## Policy",
            "",
            "LanguageTool is checked before and after SAFE edits. Gemma is called "
            "only for remaining AMBIGUOUS grammar/context evidence or unresolved "
            "contextual spelling evidence. Clean, IGNORE-only, and fully resolved "
            "cases do not route.",
            "",
            "Gemma output is accepted only when it is non-empty, text-only, "
            "structurally format-preserving, within conservative length/change "
            "budgets, and local to bounded windows around unresolved LanguageTool "
            "spans. Rejected output falls back to the SAFE text.",
            "",
            "Full original/post-SAFE LanguageTool responses, routing evidence, "
            "prompt metadata, raw Gemma output, validation decisions, final output, "
            "and per-stage latency are retained in `latest.json`.",
            "",
            "Production prompts, providers, model defaults, settings, UI, hotkeys, "
            "clipboard, sanitizer, chunking, packaging, and executable behavior were "
            "not changed.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "category",
        "original",
        "expected",
        "expected_change",
        "raw_expected_output_reachable",
        "safe_output",
        "safe_exact_match",
        "routing_decision",
        "routing_reason",
        "routing_rule_ids",
        "raw_gemma_output",
        "gemma_latency_seconds",
        "gemma_provider_error",
        "gemma_output_accepted",
        "validation_rejection_reasons",
        "final_output",
        "final_exact_match",
        "final_exact_preservation",
        "final_unnecessary_edit",
        "final_formatting_preservation",
        "improved_by_gemma",
        "made_worse_by_gemma",
        "total_latency_seconds",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "case_id": record["case_id"],
                    "category": record["category"],
                    "original": record["original"],
                    "expected": record["expected"],
                    "expected_change": record["expected_change"],
                    "raw_expected_output_reachable": record[
                        "raw_expected_output_reachable"
                    ],
                    "safe_output": record["safe_output"],
                    "safe_exact_match": record["safe_exact_match"],
                    "routing_decision": record["routing_decision"],
                    "routing_reason": record["routing_reason"],
                    "routing_rule_ids": json.dumps(
                        [
                            item["rule_id"]
                            for item in record["routing_evidence"]
                        ]
                    ),
                    "raw_gemma_output": record["raw_gemma_output"],
                    "gemma_latency_seconds": record["gemma_latency_seconds"],
                    "gemma_provider_error": record["gemma_provider_error"],
                    "gemma_output_accepted": record["gemma_output_accepted"],
                    "validation_rejection_reasons": json.dumps(
                        (
                            record["gemma_validation"] or {}
                        ).get("rejection_reasons", [])
                    ),
                    "final_output": record["final_output"],
                    "final_exact_match": record["final_exact_match"],
                    "final_exact_preservation": record[
                        "final_exact_preservation"
                    ],
                    "final_unnecessary_edit": record["final_unnecessary_edit"],
                    "final_formatting_preservation": record[
                        "final_formatting_preservation"
                    ],
                    "improved_by_gemma": record["improved_by_gemma"],
                    "made_worse_by_gemma": record["made_worse_by_gemma"],
                    "total_latency_seconds": record["latency"]["total_seconds"],
                }
            )


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    if not (1 <= args.lt_port <= 65535):
        raise ValueError("--lt-port must be between 1 and 65535")
    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    java, jar = validate_bundled_runtime(args.java, args.server_jar)
    gemma_client = ResidentOllamaClient(
        args.ollama_base_url, args.gemma_timeout, args.gemma_keep_alive
    )
    installed = gemma_client.installed_models()
    if args.model not in installed:
        raise RuntimeError(
            f"Required model {args.model!r} is not installed; installed={installed}"
        )
    if args.cold_start:
        gemma_client.unload(args.model)

    records: list[dict[str, Any]] = []
    with LanguageToolServer(
        java=java,
        server_jar=jar,
        host=args.lt_host,
        port=args.lt_port,
        startup_timeout=args.lt_startup_timeout,
    ) as server:
        lt_client = LanguageToolClient(
            server.base_url, args.lt_request_timeout
        )
        for index, case in enumerate(cases, 1):
            record = hybrid_case(case, lt_client, gemma_client, args.model)
            records.append(record)
            print(
                f"{index}/{len(cases)} {case['id']} "
                f"route={record['routing_decision']} "
                f"accepted={record['gemma_output_accepted']} "
                f"exact={record['final_exact_match']} "
                f"{record['latency']['total_seconds']:.2f}s",
                flush=True,
            )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "architecture": (
            "original LanguageTool -> Phase 18C SAFE edits -> post-SAFE "
            "LanguageTool -> evidence routing -> Gemma -> validation -> fallback"
        ),
        "benchmark_only": True,
        "language": "en-US",
        "model": args.model,
        "runtime": {
            "java": str(java),
            "server_jar": str(jar),
            "lt_base_url": f"http://{args.lt_host}:{args.lt_port}",
            "ollama_base_url": args.ollama_base_url,
            "installed_models": installed,
            "gemma_keep_alive": args.gemma_keep_alive,
            "explicit_cold_start": args.cold_start,
            "inference_options": DEFAULT_OPTIONS,
        },
        "prompt": {
            "version": PROMPT_VERSION,
            "template": HYBRID_PROMPT,
        },
        "routing_policy": {
            "allowed": [
                "remaining AMBIGUOUS grammar/context evidence",
                "unresolved contextual SAFE spelling evidence",
                "SAFE partial correction followed by unresolved evidence",
            ],
            "not_escalated": [
                "clean text",
                "IGNORE-only evidence",
                "SAFE-resolved evidence",
                "post-SAFE deterministic evidence",
            ],
        },
        "validation_policy": {
            "formatting_signature_required": True,
            "newline_tokens_required": True,
            "evidence_window_radius": 40,
            "length_delta_limit": "max(20 chars, 25% of source)",
            "change_budget": "max(16 chars, 25% of source, 12 per evidence item)",
            "ambiguous_spelling_candidate_only_output": "rejected",
            "fallback": "post-SAFE LanguageTool text",
        },
        "dataset": {
            "path": str(args.cases),
            "size": len(cases),
            "categories": dict(Counter(case["category"] for case in cases)),
        },
        "summary": summarize(records),
        "comparison": {
            "gemma_only": load_existing_gemma_baseline(
                args.gemma_only_results, args.model
            )
        },
        "case_records": records,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.results_dir / "latest.json"
    csv_path = args.results_dir / "latest.csv"
    markdown_path = args.results_dir / "latest.md"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, records)
    markdown_path.write_text(markdown_report(result), encoding="utf-8")
    print(f"Wrote {json_path}, {csv_path}, and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
