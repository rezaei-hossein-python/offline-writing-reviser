#!/usr/bin/env python3
"""Run the 105-case dataset through the production hybrid service."""

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
sys.path.insert(0, str(ROOT / "benchmarks"))

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.hybrid_service import (
    HybridProofreadingService,
)
from offline_writing_reviser.proofreading.languagetool import (
    LanguageToolRuntime,
)
from offline_writing_reviser.proofreading.policy import formatting_signature
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
)
from run_languagetool_benchmark import load_cases, percentile, safe_rate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the production-integrated hybrid policy."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("proofreading_cases.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).with_name("results")
            / "production-hybrid"
            / "latest.json"
        ),
    )
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def summarize(records: list[dict]) -> dict:
    corrections = [item for item in records if item["expected_change"]]
    correct = [
        item for item in records if item["category"] == "already_correct"
    ]
    unchanged = [item for item in records if not item["expected_change"]]
    formatting = [
        item for item in records if item["category"] == "formatting"
    ]
    latencies = [item["duration_seconds"] for item in records]
    return {
        "case_count": len(records),
        "exact_correction_accuracy": safe_rate(
            sum(item["exact"] for item in corrections), len(corrections)
        ),
        "exact_preservation": safe_rate(
            sum(item["output"] == item["input"] for item in correct),
            len(correct),
        ),
        "over_edit_rate": safe_rate(
            sum(item["output"] != item["input"] for item in unchanged),
            len(unchanged),
        ),
        "formatting_preservation": safe_rate(
            sum(item["formatting_preserved"] for item in formatting),
            len(formatting),
        ),
        "gemma_calls": sum(item["gemma_routed"] for item in records),
        "gemma_calls_on_correct_cases": sum(
            item["gemma_routed"] for item in correct
        ),
        "gemma_accepted": sum(item["gemma_accepted"] for item in records),
        "gemma_fallback": sum(item["gemma_fallback"] for item in records),
        "mean_latency_seconds": statistics.fmean(latencies),
        "median_latency_seconds": statistics.median(latencies),
        "p95_latency_seconds": percentile(latencies, 0.95),
        "routing_reasons": dict(
            Counter(
                reason
                for item in records
                for reason, count in item["routing_reasons"].items()
                for _ in range(count)
            )
        ),
    }


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    config = OfflineWritingConfig(
        model=args.model,
        timeout_seconds=args.timeout,
        max_characters=20_000,
        chunk_characters=20_000,
    )
    language_tool = LanguageToolRuntime()
    provider = OllamaCliOfflineWritingProvider(args.model)
    service = HybridProofreadingService(provider, language_tool, config)
    records: list[dict] = []
    try:
        for index, case in enumerate(cases, 1):
            result = service.revise(case["input"])
            output = result.revised_text
            record = {
                "case_id": case["id"],
                "category": case["category"],
                "input": case["input"],
                "expected": case["expected"],
                "output": output,
                "expected_change": case["input"] != case["expected"],
                "exact": output == case["expected"],
                "formatting_preserved": (
                    formatting_signature(output)
                    == formatting_signature(case["expected"])
                    if case["category"] == "formatting"
                    else None
                ),
                "duration_seconds": result.duration_ms / 1000,
                "safe_corrections": result.metadata[
                    "safe_correction_count"
                ],
                "gemma_routed": result.metadata["gemma_routed"],
                "gemma_accepted": result.metadata["gemma_accepted"],
                "gemma_fallback": result.metadata["gemma_fallback"],
                "routing_reasons": result.metadata["routing_reasons"],
                "acceleration": result.metadata["acceleration"],
            }
            records.append(record)
            print(
                f"{index}/{len(cases)} {case['id']} "
                f"routed={record['gemma_routed']} exact={record['exact']} "
                f"{record['duration_seconds']:.2f}s",
                flush=True,
            )
    finally:
        language_tool.stop()
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "production_hybrid",
        "model": args.model,
        "language": "en-US",
        "summary": summarize(records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
