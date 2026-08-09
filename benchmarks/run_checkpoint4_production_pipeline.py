from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.sequential import (
    SequentialWritingService,
    split_production_sections,
    split_sequential_sections,
)
from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionService,
    LanguageToolRuntime,
)
from offline_writing_reviser.providers.ollama import OllamaCliOfflineWritingProvider


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "results" / "phase25-checkpoint4-production.json"
SHORT_CORPUS = ROOT / "benchmarks" / "phase25_paraphrasing_cases.json"
BASELINE_CORPUS = ROOT / "benchmarks" / "phase25_baseline_cases.json"
MODEL = "qwen3:1.7b"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def unload_model(model: str) -> None:
    payload = json.dumps(
        {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def api(path: str) -> dict[str, Any]:
    with urllib.request.urlopen("http://127.0.0.1:11434" + path, timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def process_memory(pid: int | None) -> dict[str, int | None]:
    if not pid:
        return {"working_set_bytes": None, "private_bytes": None}
    command = (
        f"Get-Process -Id {pid} | Select-Object WorkingSet64,PrivateMemorySize64 "
        "| ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = {}
    return {
        "working_set_bytes": parsed.get("WorkingSet64"),
        "private_bytes": parsed.get("PrivateMemorySize64"),
    }


def run_case(service: SequentialWritingService, identifier: str, text: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = service.revise(text)
    wall_ms = (time.perf_counter() - started) * 1000
    return {
        "id": identifier,
        "input": text,
        "languagetool_text": result.languagetool_text,
        "paraphrased_text": result.paraphrased_text,
        "output": result.revised_text,
        "wall_ms": wall_ms,
        "metadata": result.metadata,
    }


def paragraph_splitter(text: str, target: int) -> list[str]:
    return split_production_sections(text, target)


def make_service(
    runtime: LanguageToolRuntime,
    provider: OllamaCliOfflineWritingProvider,
    target: int,
    splitter: Callable[[str, int], list[str]],
) -> SequentialWritingService:
    return SequentialWritingService(
        provider,
        LanguageToolCorrectionService(runtime),
        OfflineWritingConfig(model=MODEL, chunk_characters=target, timeout_seconds=60),
        section_splitter=splitter,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only-selected-long", action="store_true")
    args = parser.parse_args()
    short_cases = json.loads(SHORT_CORPUS.read_text(encoding="utf-8"))["cases"]
    long_fixture = json.loads(BASELINE_CORPUS.read_text(encoding="utf-8"))["long_case"]
    long_text = "\n\n".join(long_fixture["paragraphs"])
    provider = OllamaCliOfflineWritingProvider(MODEL)
    runtime = LanguageToolRuntime()
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "pid": os.getpid(),
        "configuration": {
            "chunk_characters": 1000,
        "strategy": "approximately_1000_character_paragraphs",
        },
    }
    report["app_only_memory"] = process_memory(os.getpid())
    try:
        lt_started = time.perf_counter()
        runtime.warmup()
        report["languagetool_warmup_ms"] = (time.perf_counter() - lt_started) * 1000
        report["languagetool_memory"] = {
            **process_memory(runtime.process.pid if runtime.process else None),
            "pid": runtime.process.pid if runtime.process else None,
        }
        if args.only_selected_long:
            service = make_service(runtime, provider, 1000, paragraph_splitter)
            report["selected_long"] = run_case(
                service, "approximately_1000_characters", long_text
            )
            report["selected_long"]["word_count"] = len(long_text.split())
            report["selected_long"]["strategy"] = (
                "approximately_1000_character_paragraphs"
            )
            running = api("/api/ps").get("models", [])
            selected = next(
                (item for item in running if item.get("name") == MODEL), {}
            )
            report["combined_runtime"] = {
                "app": process_memory(os.getpid()),
                "languagetool": process_memory(
                    runtime.process.pid if runtime.process else None
                ),
                "qwen_loaded_size_bytes": selected.get("size"),
                "qwen_vram_bytes": selected.get("size_vram"),
                "qwen_context_length": selected.get("context_length"),
                "qwen_backend": (
                    "cpu" if selected.get("size_vram") == 0 else "gpu_or_partial"
                ),
                "gemma_loaded": any(
                    item.get("name") == "gemma3:4b" for item in running
                ),
            }
            return _write_report(args.output, report, runtime)
        unload_model(MODEL)
        service = make_service(runtime, provider, 1000, paragraph_splitter)
        report["cold"] = run_case(
            service,
            "cold-awkward",
            "The meeting was very good and we discussed many important things.",
        )

        fast_inputs = {
            "spelling": "I recieved the adress yesterday.",
            "grammar": "He go to work every day.",
            "esl-mechanical": "We discussed about the project.",
        }
        fast_records = []
        for repeat in range(5):
            for identifier, text in fast_inputs.items():
                fast_records.append(run_case(service, f"{identifier}-{repeat + 1}", text))
        fast_times = [record["wall_ms"] for record in fast_records]
        report["fast_path"] = {
            "records": fast_records,
            "median_ms": statistics.median(fast_times),
            "p95_ms": percentile(fast_times, 0.95),
            "qwen_calls": sum(record["metadata"]["qwen_call_count"] for record in fast_records),
        }

        awkward_inputs = [
            "I am writing this email for informing you about the issue.",
            "The meeting was very good and we discussed many important things.",
            "The new process made a big improvement to the speed of our work.",
            "At this point in time, we are currently reviewing each and every request individually.",
            "The app is pretty good, but the setup part is kind of hard to get through.",
        ]
        paraphrase_records = [
            run_case(service, f"awkward-{index}", text)
            for index, text in enumerate(awkward_inputs, start=1)
        ]
        paraphrase_times = [record["wall_ms"] for record in paraphrase_records]
        report["paraphrase_path"] = {
            "records": paraphrase_records,
            "median_ms": statistics.median(paraphrase_times),
            "p95_ms": percentile(paraphrase_times, 0.95),
            "qwen_calls": sum(record["metadata"]["qwen_call_count"] for record in paraphrase_records),
        }

        fixed_records = [
            run_case(service, case["id"], case["input"]) for case in short_cases
        ]
        fixed_times = [record["wall_ms"] for record in fixed_records]
        report["fixed_corpus"] = {
            "records": fixed_records,
            "median_ms": statistics.median(fixed_times),
            "p95_ms": percentile(fixed_times, 0.95),
            "qwen_operations": sum(record["metadata"]["qwen_invoked"] for record in fixed_records),
            "languagetool_only_operations": sum(not record["metadata"]["qwen_invoked"] for record in fixed_records),
            "qwen_calls": sum(record["metadata"]["qwen_call_count"] for record in fixed_records),
            "qwen_rejections": sum(record["metadata"]["qwen_rejected_sections"] for record in fixed_records),
            "fallback_sections": sum(record["metadata"]["fallback_sections"] for record in fixed_records),
        }

        strategies = [
            ("whole_paragraph", 1000, paragraph_splitter),
            ("approximately_700_characters", 700, paragraph_splitter),
            ("approximately_1000_characters", 1000, paragraph_splitter),
            ("paragraph_groups_1000", 1000, split_sequential_sections),
            ("paragraph_groups_1400", 1400, split_sequential_sections),
        ]
        long_records = []
        for name, target, splitter in strategies:
            candidate_service = make_service(runtime, provider, target, splitter)
            record = run_case(candidate_service, name, long_text)
            record["strategy"] = name
            record["target_characters"] = target
            long_records.append(record)
        report["chunk_comparison"] = long_records

        running = api("/api/ps").get("models", [])
        selected = next((item for item in running if item.get("name") == MODEL), {})
        report["combined_runtime"] = {
            "app": process_memory(os.getpid()),
            "languagetool": process_memory(runtime.process.pid if runtime.process else None),
            "qwen_loaded_size_bytes": selected.get("size"),
            "qwen_vram_bytes": selected.get("size_vram"),
            "qwen_context_length": selected.get("context_length"),
            "qwen_backend": "cpu" if selected.get("size_vram") == 0 else "gpu_or_partial",
            "gemma_loaded": any(item.get("name") == "gemma3:4b" for item in running),
        }
    finally:
        if "languagetool_shutdown_ms" not in report:
            report["languagetool_shutdown_ms"] = runtime.stop()
    return _write_report(args.output, report)


def _write_report(
    output: Path,
    report: dict[str, Any],
    runtime: LanguageToolRuntime | None = None,
) -> int:
    if runtime is not None:
        report["languagetool_shutdown_ms"] = runtime.stop()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
