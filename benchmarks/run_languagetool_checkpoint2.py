#!/usr/bin/env python3
"""Benchmark the single-pass Checkpoint 2 LanguageTool correction service."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_writing_reviser.correction.languagetool import (
    JAVA_VERSION,
    LANGUAGETOOL_VERSION,
    PROTECTED_CATEGORIES,
    LanguageToolCorrectionService,
    LanguageToolRuntime,
)
from offline_writing_reviser.proofreading.semantic import protected_values


CASES = [
    {
        "id": "spelling",
        "kind": "mechanical",
        "input": "I recieved the adress yesterday.",
        "acceptable": ["I received the address yesterday."],
    },
    {
        "id": "grammar",
        "kind": "mechanical",
        "input": "He go to work every day.",
        "acceptable": ["He goes to work every day."],
    },
    {
        "id": "punctuation",
        "kind": "mechanical",
        "input": "After lunch we reviewed the contract however we did not approve it.",
        "acceptable": [
            "After lunch, we reviewed the contract; however, we did not approve it.",
            "After lunch, we reviewed the contract. However, we did not approve it.",
        ],
        "safe_outputs": [
            "After lunch, we reviewed the contract however we did not approve it."
        ],
    },
    {
        "id": "agreement",
        "kind": "mechanical",
        "input": "The list of changes are attached.",
        "acceptable": ["The list of changes is attached."],
    },
    {
        "id": "article",
        "kind": "mechanical",
        "input": "She is engineer at our Vancouver office.",
        "acceptable": ["She is an engineer at our Vancouver office."],
    },
    {
        "id": "esl-discuss",
        "kind": "mechanical",
        "input": "We discussed about the project.",
        "acceptable": ["We discussed the project."],
    },
    {
        "id": "esl-inform",
        "kind": "mechanical_optional",
        "input": "I am writing this email for informing you about the issue.",
        "acceptable": [
            "I am writing this email to inform you about the issue.",
            "I am writing this email for informing you about the issue.",
        ],
    },
    {
        "id": "correct",
        "kind": "preserve",
        "input": "The meeting starts at nine tomorrow morning.",
        "acceptable": ["The meeting starts at nine tomorrow morning."],
    },
    {
        "id": "name-organization",
        "kind": "protected",
        "input": "Priya Raman may send the report to Microsoft tomorrow.",
        "acceptable": ["Priya Raman may send the report to Microsoft tomorrow."],
    },
    {
        "id": "date-time",
        "kind": "protected",
        "input": "The review is on September 15, 2026, at 9:30 AM.",
        "acceptable": ["The review is on September 15, 2026, at 9:30 AM."],
    },
    {
        "id": "numbers-money",
        "kind": "protected",
        "input": "Jordan approved 12 licenses for CAD 1,250.50.",
        "acceptable": ["Jordan approved 12 licenses for CAD 1,250.50."],
    },
    {
        "id": "url-email-identifier-negation",
        "kind": "protected",
        "input": (
            "Do not email ops@example.com or deploy API-42; review "
            "https://example.com/release-7.2."
        ),
        "acceptable": [
            "Do not email ops@example.com or deploy API-42; review "
            "https://example.com/release-7.2."
        ],
    },
]

PARAGRAPH = (
    "I recieved the adress yesterday. He go to work every day. "
    "We discussed about the project. The meeting starts at nine tomorrow "
    "morning. Priya Raman may approve 12 licenses for CAD 1,250.50 on "
    "September 15, 2026, at 9:30 AM. Do not email ops@example.com or change "
    "API-42; see https://example.com/release-7.2."
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def process_snapshot(pid: int | None = None) -> dict[str, Any]:
    pid_filter = (
        f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"
        if pid is not None
        else "Get-Process javaw -ErrorAction SilentlyContinue"
    )
    script = (
        f"$p=@({pid_filter}); "
        "$p | Select-Object Id,ProcessName,WorkingSet64,PrivateMemorySize64,"
        "MainWindowHandle,Path | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    output = completed.stdout.strip()
    if not output:
        return {"count": 0, "processes": []}
    parsed = json.loads(output)
    processes = parsed if isinstance(parsed, list) else [parsed]
    return {"count": len(processes), "processes": processes}


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def protected_preserved(source: str, candidate: str) -> bool:
    before = protected_values(source)
    after = protected_values(candidate)
    return all(before[name] == after[name] for name in PROTECTED_CATEGORIES)


def case_record(service: LanguageToolCorrectionService, case: dict[str, Any]):
    result = service.correct(case["input"])
    return {
        "id": case["id"],
        "kind": case["kind"],
        "input": case["input"],
        "output": result.corrected_text,
        "latency_ms": result.duration_ms,
        "acceptable": result.corrected_text in case["acceptable"],
        "safe": result.corrected_text
        in [*case["acceptable"], case["input"], *case.get("safe_outputs", [])],
        "changed": result.changed,
        "protected_preserved": protected_preserved(
            case["input"], result.corrected_text
        ),
        "applied_edits": [edit.__dict__ for edit in result.applied_edits],
        "skipped_edits": [edit.__dict__ for edit in result.skipped_edits],
        "failure": result.failure.__dict__ if result.failure else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("results")
        / "phase25-languagetool-checkpoint2.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    java_root = ROOT / "vendor" / "java"
    languagetool_root = ROOT / "vendor" / "languagetool"
    runtime = LanguageToolRuntime()
    service = LanguageToolCorrectionService(runtime)
    java_before = process_snapshot()
    startup_ms = runtime.warmup()
    server_ready_ms = runtime.startup_duration_ms
    pid = runtime.process.pid if runtime.process else None
    java_running = process_snapshot()

    records = [case_record(service, case) for case in CASES]
    warm_sentence_ms = []
    for _ in range(3):
        for case in CASES:
            warm_sentence_ms.append(service.correct(case["input"]).duration_ms)
    warm_paragraph_ms = [service.correct(PARAGRAPH).duration_ms for _ in range(5)]
    server_memory = process_snapshot(pid)

    shutdown_ms = runtime.stop()
    time.sleep(0.1)
    java_after = process_snapshot()

    required = [record for record in records if record["kind"] == "mechanical"]
    all_acceptable = [record for record in records if record["acceptable"]]
    incorrect_edits = [
        record
        for record in records
        if record["changed"] and not record["safe"]
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "architecture": "single_pass_languagetool_correction",
        "language": "en-US",
        "languagetool_version": LANGUAGETOOL_VERSION,
        "java_version": JAVA_VERSION,
        "runtime_sizes": {
            "java_bytes": directory_size(java_root),
            "languagetool_bytes": directory_size(languagetool_root),
            "combined_bytes": directory_size(java_root)
            + directory_size(languagetool_root),
        },
        "performance": {
            "first_startup_ms": startup_ms,
            "server_ready_ms": server_ready_ms,
            "first_correction_ms": records[0]["latency_ms"],
            "warm_sentence_median_ms": statistics.median(warm_sentence_ms),
            "warm_sentence_p95_ms": percentile(warm_sentence_ms, 0.95),
            "warm_sentence_max_ms": max(warm_sentence_ms),
            "warm_paragraph_median_ms": statistics.median(warm_paragraph_ms),
            "warm_paragraph_p95_ms": percentile(warm_paragraph_ms, 0.95),
            "shutdown_ms": shutdown_ms,
        },
        "quality": {
            "required_mechanical_case_count": len(required),
            "required_mechanical_reach": sum(
                record["acceptable"] for record in required
            ),
            "useful_mechanical_reach": sum(
                record["acceptable"] or (record["changed"] and record["safe"])
                for record in required
            ),
            "acceptable_case_count": len(all_acceptable),
            "case_count": len(records),
            "incorrect_edit_count": len(incorrect_edits),
            "incorrect_edit_rate": len(incorrect_edits) / len(records),
            "protected_preservation_count": sum(
                record["protected_preserved"] for record in records
            ),
            "protected_preservation_rate": sum(
                record["protected_preserved"] for record in records
            )
            / len(records),
        },
        "process": {
            "javaw_before": java_before,
            "javaw_while_running": java_running,
            "server_memory": server_memory,
            "javaw_after_shutdown": java_after,
            "owned_pid": pid,
            "zero_visible_windows": all(
                not item.get("MainWindowHandle")
                for item in server_memory["processes"]
            ),
            "owned_process_stopped": runtime.process is None,
        },
        "records": records,
        "paragraph": {
            "input": PARAGRAPH,
            "samples_ms": warm_paragraph_ms,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({**report["performance"], **report["quality"]}, indent=2))
    print(json.dumps(report["process"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
