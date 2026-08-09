#!/usr/bin/env python3
"""Measure the unchanged v0.4.0 production engine for Phase 25."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


class TelemetryProvider:
    """Preserve the production provider path while retaining Ollama telemetry."""

    def __init__(self, provider: OllamaCliOfflineWritingProvider):
        self.provider = provider
        self.telemetry: list[dict[str, Any]] = []
        self.request_count = 0

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model_identifier(self) -> str:
        return self.provider.model_identifier

    def is_available(self) -> bool:
        return self.provider.is_available()

    def cancel_current(self) -> None:
        self.provider.cancel_current()

    def revise(self, text: str, instruction: str, timeout_seconds: float) -> str:
        self.request_count += 1
        result = self.provider.revise_with_telemetry(
            text, instruction, timeout_seconds
        )
        self.telemetry.append(dict(result.telemetry))
        return result.text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("phase25_baseline_cases.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("results")
        / "phase25-v0.4.0-baseline.json",
    )
    return parser.parse_args()


def model_details(provider: OllamaCliOfflineWritingProvider) -> dict[str, Any]:
    payload = provider._request_json("/api/tags", None, 5.0)
    selected = next(
        (
            item
            for item in payload.get("models", [])
            if isinstance(item, dict)
            and item.get("name")
            in {provider.model_identifier, f"{provider.model_identifier}:latest"}
        ),
        {},
    )
    return {
        "name": selected.get("name"),
        "size_bytes": selected.get("size"),
        "parameter_size": (selected.get("details") or {}).get("parameter_size"),
        "quantization_level": (selected.get("details") or {}).get(
            "quantization_level"
        ),
    }


def run_case(
    service: OfflineWritingService,
    telemetry_provider: TelemetryProvider,
    case: dict[str, Any],
) -> dict[str, Any]:
    request_start = telemetry_provider.request_count
    telemetry_start = len(telemetry_provider.telemetry)
    started = time.perf_counter()
    error: str | None = None
    try:
        result = service.revise(case["input"])
        output = result.revised_text
        metadata = dict(result.metadata)
    except Exception as exc:  # benchmark records production failures verbatim
        output = case["input"]
        metadata = {}
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    validation = validate_semantic_preservation(case["input"], output)
    telemetry = telemetry_provider.telemetry[telemetry_start:]
    return {
        "id": case["id"],
        "category": case["category"],
        "input": case["input"],
        "output": output,
        "expected_change": case.get("expected_change"),
        "changed": output != case["input"],
        "exact_acceptable": output in case.get("acceptable", []),
        "semantic_preservation": validation.accepted,
        "semantic_reasons": list(validation.reasons),
        "latency_seconds": elapsed,
        "request_count": telemetry_provider.request_count - request_start,
        "model_telemetry": telemetry,
        "chunk_count": metadata.get("chunk_count"),
        "unsafe_chunks": metadata.get("unsafe_chunks", 0),
        "timeout_chunks": metadata.get("timeout_chunks", 0),
        "error": error,
    }


def main() -> int:
    args = parse_args()
    fixture = json.loads(args.cases.read_text(encoding="utf-8"))
    config = OfflineWritingConfig()
    provider = OllamaCliOfflineWritingProvider(config.model, config.ollama_executable)
    provider.ensure_api_running(timeout_seconds=20.0)
    details = model_details(provider)
    telemetry_provider = TelemetryProvider(provider)
    service = OfflineWritingService(telemetry_provider, config)

    records = [
        run_case(service, telemetry_provider, case) for case in fixture["cases"]
    ]
    long_fixture = fixture["long_case"]
    long_input = "\n\n".join(long_fixture["paragraphs"])
    long_case = {
        **long_fixture,
        "input": long_input,
        "expected_change": True,
        "acceptable": [],
    }
    long_record = run_case(service, telemetry_provider, long_case)
    long_record["word_count"] = len(long_input.split())

    warm_latencies = [record["latency_seconds"] for record in records[1:]]
    all_telemetry = [
        item for record in records + [long_record] for item in record["model_telemetry"]
    ]
    runtime = provider.runtime_diagnostics(timeout_seconds=5.0)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": "v0.4.0",
        "architecture": "unified_intelligent_revision",
        "model": config.model,
        "model_details": details,
        "runtime": runtime,
        "prompt": REVISION_INSTRUCTION,
        "first_token_time": {
            "value": None,
            "status": "not_exposed_by_v0.4.0_provider",
        },
        "summary": {
            "case_count": len(records),
            "cold_latency_seconds": records[0]["latency_seconds"],
            "warm_median_seconds": statistics.median(warm_latencies),
            "warm_p95_seconds": percentile(warm_latencies, 0.95),
            "request_count": sum(record["request_count"] for record in records),
            "total_duration_seconds": sum(
                record["latency_seconds"] for record in records
            ),
            "model_load_seconds": sum(
                item.get("load_duration_seconds") or 0 for item in all_telemetry
            ),
            "inference_seconds": sum(
                item.get("generation_duration_seconds") or 0
                for item in all_telemetry
            ),
            "unchanged_rate": sum(not record["changed"] for record in records)
            / len(records),
            "exact_acceptable_rate": sum(
                record["exact_acceptable"] for record in records
            )
            / len(records),
            "semantic_preservation_rate": sum(
                record["semantic_preservation"] for record in records
            )
            / len(records),
            "unsafe_rejection_rate": sum(
                bool(record["unsafe_chunks"]) for record in records
            )
            / len(records),
            "timeout_rate": sum(bool(record["timeout_chunks"]) for record in records)
            / len(records),
            "error_rate": sum(bool(record["error"]) for record in records)
            / len(records),
        },
        "records": records,
        "long_text": long_record,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], indent=2))
    print(
        json.dumps(
            {
                "long_text_words": long_record["word_count"],
                "long_text_latency_seconds": long_record["latency_seconds"],
                "long_text_requests": long_record["request_count"],
                "long_text_unsafe_chunks": long_record["unsafe_chunks"],
                "runtime": runtime,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
