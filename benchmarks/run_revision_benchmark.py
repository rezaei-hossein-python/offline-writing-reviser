#!/usr/bin/env python3
"""Benchmark the exact production intelligent-revision service."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.windows.controller import build_production_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the installed production revision architecture."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("semantic_quality_cases.json"),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("results") / "revision-latest.json",
    )
    parser.add_argument(
        "--long-text",
        action="store_true",
        help="Also measure approximately 100, 500, 1,000, and 2,000 words.",
    )
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.5))
    return ordered[index]


def main() -> int:
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    if args.limit:
        cases = cases[: args.limit]
    config = OfflineWritingConfig()
    service = build_production_service(config)
    records = []
    for index, case in enumerate(cases):
        started = time.perf_counter()
        result = service.revise(case["input"])
        elapsed = time.perf_counter() - started
        validation = validate_semantic_preservation(
            case["input"], result.revised_text
        )
        acceptable = case.get("acceptable", [])
        records.append(
            {
                "id": case["id"],
                "group": case.get("group"),
                "target": case.get("target"),
                "expected_change": case.get("expected_change"),
                "output": result.revised_text,
                "exact_acceptable": result.revised_text in acceptable,
                "semantic_preservation": validation.accepted,
                "semantic_reasons": list(validation.reasons),
                "changed": result.revised_text != case["input"],
                "latency_seconds": elapsed,
                "temperature": 0,
                "cold": index == 0,
            }
        )

    long_records = []
    if args.long_text:
        paragraph = (
            "I am writing this email for informing you about the issue. "
            "The meeting was very good and we discussed about many important "
            "things. The next review is on September 15 at 9:30 AM, costs "
            "$125, and I do not approve any change to ticket OWR-2048."
        )
        for target_words in (100, 500, 1000, 2000):
            repeats = max(1, target_words // len(paragraph.split()))
            source = "\n\n".join(paragraph for _ in range(repeats))
            started = time.perf_counter()
            result = service.revise(source)
            elapsed = time.perf_counter() - started
            validation = validate_semantic_preservation(
                source, result.revised_text
            )
            long_records.append(
                {
                    "target_words": target_words,
                    "actual_words": len(source.split()),
                    "input_chars": len(source),
                    "output_chars": len(result.revised_text),
                    "complete": bool(result.revised_text.strip()),
                    "semantic_preservation": validation.accepted,
                    "semantic_reasons": list(validation.reasons),
                    "latency_seconds": elapsed,
                    "chunk_count": result.metadata.get("chunk_count"),
                    "chunk_durations_ms": result.metadata.get(
                        "chunk_durations_ms", []
                    ),
                    "successful_chunks": result.metadata.get(
                        "successful_chunks", 0
                    ),
                    "preserved_chunks": result.metadata.get(
                        "preserved_chunks", 0
                    ),
                    "timeout_chunks": result.metadata.get("timeout_chunks", 0),
                    "unsafe_chunks": result.metadata.get("unsafe_chunks", 0),
                    "output_complete": len(result.revised_text) > 0,
                }
            )

    latencies = [record["latency_seconds"] for record in records]
    warm = latencies[1:]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "architecture": "unified_intelligent_revision",
        "model": config.model,
        "case_count": len(records),
        "summary": {
            "exact_acceptable_rate": (
                sum(record["exact_acceptable"] for record in records)
                / len(records)
                if records
                else None
            ),
            "semantic_preservation_rate": (
                sum(record["semantic_preservation"] for record in records)
                / len(records)
                if records
                else None
            ),
            "cold_latency_seconds": latencies[0] if latencies else None,
            "warm_mean_seconds": statistics.mean(warm) if warm else None,
            "warm_median_seconds": statistics.median(warm) if warm else None,
            "warm_p95_seconds": percentile(warm, 0.95),
        },
        "records": records,
        "long_text": long_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
