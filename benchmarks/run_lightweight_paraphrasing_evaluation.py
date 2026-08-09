from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offline_writing_reviser.core.errors import OfflineWritingMalformedOutput
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionService,
    LanguageToolRuntime,
)
from offline_writing_reviser.proofreading.semantic import (
    meaning_anchor_preserved,
    restore_source_number_formatting,
    restore_source_word_casing,
    validate_semantic_preservation,
)


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "benchmarks" / "phase25_paraphrasing_cases.json"
DEFAULT_OUTPUT = ROOT / "benchmarks" / "results" / "phase25-lightweight-raw.json"
OLLAMA_URL = "http://127.0.0.1:11434"
MODELS = ("gemma3:1b", "qwen3:1.7b", "llama3.2:1b")
PROMPT = """Rewrite the text in clear, natural, fluent English.

Improve phrasing, vocabulary, clarity, conciseness, and sentence flow only where useful.

Preserve the complete meaning, purpose, facts, names, organizations, numbers, dates, times, amounts, URLs, email addresses, identifiers, negation, modality, questions, commitments, and intent.

Do not add information.

If the text is already natural and well written, return it unchanged.

Return only the final text.

No explanation. No commentary. No Markdown. No labels. No quotation wrapper. No reasoning."""
OPTIONS = {
    "temperature": 0.2,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "seed": 25,
    "num_ctx": 4096,
    "num_predict": 384,
}


def api(path: str, payload: dict[str, Any] | None = None, timeout: float = 60) -> dict[str, Any]:
    request = urllib.request.Request(
        OLLAMA_URL + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid Ollama response from {path}")
    return value


def unload(model: str) -> None:
    api("/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": 0})


def stream_chat(model: str, text: str, timeout: float = 90) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": True,
        "think": False,
        "keep_alive": "10m",
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text},
        ],
        "options": OPTIONS,
    }
    request = urllib.request.Request(
        OLLAMA_URL + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_token_ms: float | None = None
    pieces: list[str] = []
    final: dict[str, Any] = {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            item = json.loads(line.decode("utf-8"))
            message = item.get("message") if isinstance(item, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                pieces.append(content)
            if isinstance(item, dict):
                final = item
            if item.get("done") is True:
                break
    wall_ms = (time.perf_counter() - started) * 1000
    eval_count = _integer(final, "eval_count")
    eval_duration_ms = _ns_ms(final, "eval_duration")
    return {
        "output": "".join(pieces),
        "wall_ms": wall_ms,
        "first_token_ms": first_token_ms,
        "total_duration_ms": _ns_ms(final, "total_duration"),
        "load_duration_ms": _ns_ms(final, "load_duration"),
        "prompt_eval_duration_ms": _ns_ms(final, "prompt_eval_duration"),
        "generation_duration_ms": eval_duration_ms,
        "input_tokens": _integer(final, "prompt_eval_count"),
        "output_tokens": eval_count,
        "tokens_per_second": (
            eval_count / (eval_duration_ms / 1000)
            if eval_count and eval_duration_ms
            else None
        ),
        "done_reason": final.get("done_reason"),
        "thinking": final.get("thinking", ""),
    }


def evaluate_output(original: str, lt_text: str, raw: str) -> dict[str, Any]:
    try:
        revised = sanitize_revision_output(raw, original_text=lt_text)
        revised = restore_source_number_formatting(lt_text, revised)
        revised = restore_source_word_casing(lt_text, revised)
    except OfflineWritingMalformedOutput as exc:
        return {
            "sanitized_output": None,
            "validator_accepted": False,
            "rejection_reason": exc.reason,
            "semantic_reasons": [],
            "meaning_anchor_preserved": False,
        }
    validation = validate_semantic_preservation(original, revised)
    anchors = meaning_anchor_preserved(original, revised)
    reasons = list(validation.reasons)
    if not anchors:
        reasons.append("meaning_anchor_not_preserved")
    return {
        "sanitized_output": revised,
        "validator_accepted": validation.accepted and anchors,
        "rejection_reason": None if validation.accepted and anchors else ",".join(reasons),
        "semantic_reasons": reasons,
        "meaning_anchor_preserved": anchors,
    }


def model_details(model: str) -> dict[str, Any]:
    shown = api("/api/show", {"model": model})
    tags = api("/api/tags").get("models", [])
    tag = next((item for item in tags if item.get("name") == model), {})
    return {
        "name": model,
        "digest": tag.get("digest"),
        "installed_size_bytes": tag.get("size"),
        "download_size_bytes": tag.get("size"),
        "parameter_count": shown.get("details", {}).get("parameter_size"),
        "quantization": shown.get("details", {}).get("quantization_level"),
        "family": shown.get("details", {}).get("family"),
    }


def runtime_details(model: str) -> dict[str, Any]:
    running = api("/api/ps").get("models", [])
    item = next((entry for entry in running if entry.get("name") == model), {})
    size = item.get("size")
    size_vram = item.get("size_vram")
    if size_vram == 0:
        backend = "cpu"
    elif size and size_vram and size_vram >= size * 0.95:
        backend = "gpu"
    elif size_vram:
        backend = "partial_gpu"
    else:
        backend = "unknown"
    return {
        "loaded_size_bytes": size,
        "vram_bytes": size_vram,
        "backend": backend,
        "context_length": item.get("context_length"),
        "expires_at": item.get("expires_at"),
    }


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    sentence = [r["telemetry"]["wall_ms"] for r in records if "paragraph" not in r["category"]]
    paragraph = [r["telemetry"]["wall_ms"] for r in records if "paragraph" in r["category"]]
    return {
        "warm_sentence_runs": len(sentence),
        "warm_sentence_median_ms": statistics.median(sentence),
        "warm_sentence_p95_ms": percentile(sentence, 0.95),
        "warm_paragraph_runs": len(paragraph),
        "warm_paragraph_median_ms": statistics.median(paragraph),
        "warm_paragraph_p95_ms": percentile(paragraph, 0.95),
        "validator_accepted": sum(r["evaluation"]["validator_accepted"] for r in records),
        "validator_rejected": sum(not r["evaluation"]["validator_accepted"] for r in records),
        "commentary_or_malformed": sum(
            r["evaluation"]["rejection_reason"] in {"commentary", "markdown_wrapper", "json_wrapper", "xml_wrapper"}
            for r in records
        ),
        "timeouts": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    args = parser.parse_args()
    corpus = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    runtime = LanguageToolRuntime()
    correction = LanguageToolCorrectionService(runtime)
    prepared: list[dict[str, Any]] = []
    try:
        runtime.warmup()
        for case in corpus:
            result = correction.correct(case["input"])
            if result.failure:
                raise RuntimeError(result.failure.message)
            prepared.append({**case, "languagetool_text": result.corrected_text})
    finally:
        runtime.stop()

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "architecture": "languagetool_then_single_lightweight_paraphrase_then_original_validation",
        "prompt": PROMPT,
        "settings": {**OPTIONS, "think": False, "stream": True, "keep_alive": "10m"},
        "corpus_path": str(CASES_PATH.relative_to(ROOT)),
        "case_count": len(prepared),
        "models": [],
    }
    for model in args.models:
        unload(model)
        cold = stream_chat(model, prepared[2]["languagetool_text"])
        records: list[dict[str, Any]] = []
        for case in prepared:
            telemetry = stream_chat(model, case["languagetool_text"])
            evaluation = evaluate_output(case["input"], case["languagetool_text"], telemetry["output"])
            records.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "benefit": case["benefit"],
                    "original_text": case["input"],
                    "languagetool_text": case["languagetool_text"],
                    "candidate_output": telemetry.pop("output"),
                    "evaluation": evaluation,
                    "telemetry": telemetry,
                }
            )
        report["models"].append(
            {
                "details": model_details(model),
                "cold": cold,
                "runtime": runtime_details(model),
                "summary": summarize(records),
                "records": records,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    return 0


def _ns_ms(value: dict[str, Any], key: str) -> float | None:
    raw = value.get(key)
    return raw / 1_000_000 if isinstance(raw, int) else None


def _integer(value: dict[str, Any], key: str) -> int | None:
    raw = value.get(key)
    return raw if isinstance(raw, int) else None


if __name__ == "__main__":
    raise SystemExit(main())
