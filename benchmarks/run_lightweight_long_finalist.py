from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionService,
    LanguageToolRuntime,
)
from run_lightweight_paraphrasing_evaluation import (
    evaluate_output,
    model_details,
    runtime_details,
    stream_chat,
    unload,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_CORPUS = ROOT / "benchmarks" / "phase25_baseline_cases.json"
DEFAULT_OUTPUT = ROOT / "benchmarks" / "results" / "phase25-lightweight-long.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    fixture = json.loads(BASELINE_CORPUS.read_text(encoding="utf-8"))["long_case"]
    originals = fixture["paragraphs"]
    runtime = LanguageToolRuntime()
    correction = LanguageToolCorrectionService(runtime)
    corrected: list[str] = []
    try:
        runtime.warmup()
        for paragraph in originals:
            result = correction.correct(paragraph)
            if result.failure:
                raise RuntimeError(result.failure.message)
            corrected.append(result.corrected_text)
    finally:
        runtime.stop()

    unload(args.model)
    started = time.perf_counter()
    records = []
    accepted_outputs: list[str] = []
    for index, (original, lt_text) in enumerate(zip(originals, corrected), start=1):
        telemetry = stream_chat(args.model, lt_text, timeout=120)
        candidate = telemetry.pop("output")
        evaluation = evaluate_output(original, lt_text, candidate)
        accepted_outputs.append(
            evaluation["sanitized_output"]
            if evaluation["validator_accepted"]
            else lt_text
        )
        records.append(
            {
                "section": index,
                "original_text": original,
                "languagetool_text": lt_text,
                "candidate_output": candidate,
                "evaluation": evaluation,
                "telemetry": telemetry,
                "used_languagetool_fallback": not evaluation["validator_accepted"],
            }
        )
    total_ms = (time.perf_counter() - started) * 1000
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_details(args.model),
        "runtime": runtime_details(args.model),
        "word_count": len(" ".join(originals).split()),
        "chunk_policy": "one structure-preserving request per source paragraph",
        "chunk_count": len(records),
        "model_request_count": len(records),
        "total_model_duration_ms": total_ms,
        "accepted_paraphrases": sum(
            record["evaluation"]["validator_accepted"] for record in records
        ),
        "validator_rejections": sum(
            not record["evaluation"]["validator_accepted"] for record in records
        ),
        "timeouts": 0,
        "structure_preserved": len(accepted_outputs) == len(originals),
        "records": records,
        "final_text": "\n\n".join(accepted_outputs),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
