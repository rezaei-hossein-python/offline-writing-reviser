#!/usr/bin/env python3
"""Evaluate production proofreading without treating one reference as unique."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.hybrid_service import HybridProofreadingService
from offline_writing_reviser.proofreading.languagetool import LanguageToolRuntime
from offline_writing_reviser.proofreading.policy import (
    detect_language_quality_signals,
    normalize_matches,
    route_post_safe,
    safe_filter,
)
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
)


FACT_REASONS = {
    "urls_not_preserved",
    "emails_not_preserved",
    "phones_not_preserved",
    "numbers_not_preserved",
    "dates_not_preserved",
    "times_not_preserved",
    "identifiers_not_preserved",
    "quotes_not_preserved",
    "names_not_preserved",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the focused Phase 21 semantic-quality benchmark."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("semantic_quality_cases.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).with_name("results")
            / "semantic-quality"
            / "latest.json"
        ),
    )
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def classify(
    case: dict, output: str, semantic_reasons: list[str], quality_clean: bool
) -> str:
    if semantic_reasons:
        return (
            "FACTUAL REGRESSION"
            if FACT_REASONS.intersection(semantic_reasons)
            else "SEMANTIC REGRESSION"
        )
    if not case["expected_change"]:
        return (
            "UNCHANGED-BUT-CORRECT"
            if output == case["input"]
            else "UNNECESSARY REWRITE"
        )
    if output == case["input"]:
        return "MISSED CORRECTION"
    if output in case["acceptable"]:
        return (
            "GOOD CORRECTION"
            if case["target"] in {"grammar", "professional email grammar"}
            else "GOOD MEANING-PRESERVING IMPROVEMENT"
        )
    if quality_clean:
        return "GOOD MEANING-PRESERVING IMPROVEMENT"
    return "INCOMPLETE CORRECTION"


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def main() -> int:
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]
    config = OfflineWritingConfig(
        model=args.model,
        timeout_seconds=args.timeout,
        max_characters=20_000,
        chunk_characters=20_000,
    )
    language_tool = LanguageToolRuntime()
    service = HybridProofreadingService(
        OllamaCliOfflineWritingProvider(args.model),
        language_tool,
        config,
    )
    records = []
    try:
        for index, case in enumerate(cases, 1):
            result = service.revise(case["input"])
            output = result.revised_text
            semantic = validate_semantic_preservation(case["input"], output)
            payload, post_latency = language_tool.check(output)
            matches = normalize_matches(payload, output)
            _, decisions, _ = safe_filter(output, matches)
            deterministic_errors = sum(
                decision["accepted"] for decision in decisions
            )
            quality_signals = detect_language_quality_signals(output)
            post_routing = route_post_safe(
                matches, decisions, 0, output
            )
            quality_clean = (
                not deterministic_errors
                and not post_routing["route_to_gemma"]
            )
            classification = classify(
                case, output, list(semantic.reasons), quality_clean
            )
            record = {
                **case,
                "output": output,
                "classification": classification,
                "semantic_preserved": semantic.accepted,
                "factual_preserved": not bool(
                    FACT_REASONS.intersection(semantic.reasons)
                ),
                "grammar_correct": quality_clean,
                "spelling_correct": deterministic_errors == 0,
                "naturalness_signal_count": len(quality_signals),
                "acceptable_reference": output in case["acceptable"],
                "changed": output != case["input"],
                "duration_seconds": result.duration_ms / 1000,
                "postcheck_seconds": post_latency,
                "gemma_routed": result.metadata["gemma_routed"],
                "gemma_accepted": result.metadata["gemma_accepted"],
                "gemma_fallback": result.metadata["gemma_fallback"],
                "semantic_reasons": list(semantic.reasons),
            }
            records.append(record)
            print(
                f"{index}/{len(cases)} {case['id']} "
                f"{classification} {record['duration_seconds']:.2f}s",
                flush=True,
            )
    finally:
        language_tool.stop()

    correct = [item for item in records if not item["expected_change"]]
    changes = [item for item in records if item["expected_change"]]
    classifications = Counter(item["classification"] for item in records)
    latencies = [item["duration_seconds"] for item in records]
    summary = {
        "case_count": len(records),
        "semantic_preservation": rate(
            sum(item["semantic_preserved"] for item in records), len(records)
        ),
        "factual_preservation": rate(
            sum(item["factual_preserved"] for item in records), len(records)
        ),
        "grammar_correctness": rate(
            sum(item["grammar_correct"] for item in records), len(records)
        ),
        "spelling_correctness": rate(
            sum(item["spelling_correct"] for item in records), len(records)
        ),
        "unnecessary_edit_rate": rate(
            sum(item["changed"] for item in correct), len(correct)
        ),
        "harmful_change_rate": rate(
            classifications["SEMANTIC REGRESSION"]
            + classifications["FACTUAL REGRESSION"],
            len(records),
        ),
        "reference_or_valid_alternative_success": rate(
            sum(
                item["classification"]
                in {
                    "UNCHANGED-BUT-CORRECT",
                    "GOOD CORRECTION",
                    "GOOD MEANING-PRESERVING IMPROVEMENT",
                }
                for item in records
            ),
            len(records),
        ),
        "change_case_success": rate(
            sum(
                item["classification"]
                in {
                    "GOOD CORRECTION",
                    "GOOD MEANING-PRESERVING IMPROVEMENT",
                }
                for item in changes
            ),
            len(changes),
        ),
        "classifications": dict(classifications),
        "gemma_calls": sum(item["gemma_routed"] for item in records),
        "mean_latency_seconds": statistics.fmean(latencies),
        "median_latency_seconds": statistics.median(latencies),
    }
    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "phase21_semantic_quality",
        "model": args.model,
        "summary": summary,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
