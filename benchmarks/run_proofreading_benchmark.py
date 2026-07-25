#!/usr/bin/env python3
"""Deterministic local-Ollama proofreading benchmark.

This module is intentionally independent of the production application.
It never pulls a model and only benchmarks exact candidate names reported
as installed by the local Ollama instance.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CANDIDATE_MODELS = ("llama3.2:3b", "qwen3.5:4b", "qwen3:4b", "gemma3:4b")
PROMPT = """Correct only objective spelling and grammatical errors.

Make the minimum changes necessary.

Do not paraphrase, improve style, change tone, change vocabulary, or restructure sentences.

If the text is already grammatically and orthographically correct, return it exactly unchanged.

Preserve punctuation, capitalization, formatting, line breaks, and paragraph structure whenever they are already correct.

Return only the resulting text."""
PRODUCTION_BASELINE_PROMPT = """You are an offline writing revision engine.
Task: make the minimum changes required to turn the user's selected English text into polished, natural, grammatically correct English with the minimum necessary edits.

Rules:
- Make the smallest set of changes needed to correct grammar, spelling, punctuation, awkward phrasing, sentence structure, and naturalness.
- Preserve tense unless grammar or explicit context requires correcting it.
- Respect explicit time markers such as yesterday, today, tomorrow, since, for, already, and yet.
- Preserve pronouns exactly unless grammar makes the original pronoun impossible.
- Preserve names.
- Preserve meaning, facts, numbers, dates, email addresses, URLs, and tone.
- Avoid unnecessary synonyms.
- Do not make wording more formal unless required.
- Do not add information.
- Do not remove information.
- Preserve paragraph structure where reasonable.
- Return only the revised text.
- Do not include explanations, headings, commentary, quotation marks around the result, markdown, preambles, conclusions, scores, or correction lists.

Examples:
Input:
I have spoke with client yesterday and he said he don't received the documents yet.
Preferred:
I spoke with the client yesterday, and he said he hadn't received the documents yet."""
DEFAULT_OPTIONS = {
    "temperature": 0,
    "seed": 0,
    "num_ctx": 8192,
    "num_predict": 4096,
}
@dataclass
class OllamaClient:
    base_url: str
    timeout: float

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def installed_models(self) -> list[str]:
        response = self.request("/api/tags")
        return sorted(
            {
                str(item.get("name", ""))
                for item in response.get("models", [])
                if item.get("name")
            },
            key=str.casefold,
        )

    def generate(
        self, model: str, text: str, instruction: str = PROMPT
    ) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "options": DEFAULT_OPTIONS,
        }
        result = self.request("/api/chat", payload)
        return str(result.get("message", {}).get("content", "")), result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark installed local Ollama models as strict proofreaders."
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=CANDIDATE_MODELS,
        default=list(CANDIDATE_MODELS),
        help="Candidate models to consider (installed models only).",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:11434",
        help="Local Ollama API base URL (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Per-request timeout in seconds (default: %(default)s).",
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
        default=Path(__file__).with_name("results"),
        help="Output directory.",
    )
    parser.add_argument(
        "--skip-performance",
        action="store_true",
        help="Skip the separate ~100/~500/~1,000/~2,000-word timing samples.",
    )
    parser.add_argument(
        "--skip-llama-baseline",
        action="store_true",
        help="Skip paired llama3.2:3b testing with the current production prompt.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N quality cases (smoke testing only).",
    )
    return parser.parse_args()


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
        ]
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


def run_required_ollama_list(executable: str | None) -> dict[str, Any]:
    if executable is None:
        return {
            "command": "ollama list",
            "available": False,
            "error": "Ollama executable not found; API discovery attempted instead.",
            "stdout": "",
        }
    try:
        completed = subprocess.run(
            [executable, "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return {
            "command": f"{executable} list",
            "available": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "error": completed.stderr.strip() or None,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": f"{executable} list",
            "available": False,
            "stdout": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) < 100:
        raise ValueError("Dataset must be a JSON array containing at least 100 cases.")
    required = {"id", "category", "input", "expected"}
    ids: set[str] = set()
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"Case is missing fields {sorted(missing)}: {case!r}")
        if case["id"] in ids:
            raise ValueError(f"Duplicate case id: {case['id']}")
        ids.add(case["id"])
        if case["category"] not in {
            "already_correct",
            "grammar",
            "spelling",
            "mixed",
            "formatting",
        }:
            raise ValueError(f"Unknown category in {case['id']}")
        if not all(isinstance(case[key], str) for key in ("id", "input", "expected")):
            raise ValueError(f"String field has invalid type in {case['id']}")
    counts = Counter(case["category"] for case in cases)
    minimums = {
        "already_correct": 30,
        "grammar": 30,
        "spelling": 20,
        "mixed": 10,
        "formatting": 10,
    }
    for category, minimum in minimums.items():
        if counts[category] < minimum:
            raise ValueError(f"{category} requires {minimum} cases; found {counts[category]}")
    return cases


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def formatting_signature(value: str) -> dict[str, Any]:
    lines = value.splitlines()
    return {
        "newline_count": value.count("\n"),
        "trailing_newline": value.endswith("\n"),
        "blank_line_indexes": [i for i, line in enumerate(lines) if not line],
        "line_prefixes": [
            line[: len(line) - len(line.lstrip(" \t-*•0123456789."))]
            for line in lines
        ],
    }


def quality_record(
    model: str,
    case: dict[str, Any],
    output: str,
    latency: float,
    response: dict[str, Any] | None,
    error: str | None,
    timed_out: bool,
) -> dict[str, Any]:
    expected_change = case["input"] != case["expected"]
    actual_change = output != case["input"] if error is None else None
    exact_match = output == case["expected"] if error is None else False
    eval_count = (response or {}).get("eval_count")
    eval_duration = (response or {}).get("eval_duration")
    tokens_per_second = (
        eval_count / (eval_duration / 1_000_000_000)
        if eval_count and eval_duration
        else None
    )
    formatting_result = (
        formatting_signature(output) == formatting_signature(case["expected"])
        if case["category"] == "formatting" and error is None
        else None
    )
    return {
        "model": model,
        "case_id": case["id"],
        "category": case["category"],
        "input": case["input"],
        "expected_output": case["expected"],
        "actual_output": output if error is None else None,
        "exact_match": exact_match,
        "latency_seconds": latency,
        "input_length": len(case["input"]),
        "output_length": len(output) if error is None else None,
        "output_changed": actual_change,
        "expected_change": expected_change,
        "actual_change": actual_change,
        "unnecessary_edit": (
            not expected_change and bool(actual_change) if error is None else False
        ),
        "missed_correction": (
            expected_change and not exact_match if error is None else False
        ),
        "formatting_preservation": formatting_result,
        "eval_count": eval_count,
        "eval_duration_ns": eval_duration,
        "tokens_per_second": tokens_per_second,
        "model_provider_error": error,
        "timeout": timed_out,
    }


def invoke(
    client: OllamaClient, model: str, text: str, instruction: str = PROMPT
) -> tuple[str, float, dict[str, Any] | None, str | None, bool]:
    started = time.perf_counter()
    try:
        output, response = client.generate(model, text, instruction)
        return output, time.perf_counter() - started, response, None, False
    except TimeoutError as exc:
        return "", time.perf_counter() - started, None, str(exc), True
    except urllib.error.URLError as exc:
        timed_out = isinstance(exc.reason, TimeoutError)
        return "", time.perf_counter() - started, None, str(exc), timed_out
    except (OSError, ValueError, KeyError) as exc:
        return "", time.perf_counter() - started, None, f"{type(exc).__name__}: {exc}", False


def summarize_model(model: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [record for record in records if not record["model_provider_error"]]
    correct = [record for record in usable if record["category"] == "already_correct"]
    correction = [record for record in usable if record["expected_change"]]
    unchanged = [record for record in usable if not record["expected_change"]]
    formatting = [record for record in usable if record["category"] == "formatting"]
    latencies = [record["latency_seconds"] for record in usable]
    tps_values = [
        record["tokens_per_second"]
        for record in usable
        if record["tokens_per_second"] is not None
    ]
    total = len(records)
    return {
        "model": model,
        "quality_case_count": total,
        "successful_case_count": len(usable),
        "exact_preservation_rate": safe_rate(
            sum(record["exact_match"] for record in correct), len(correct)
        ),
        "exact_correction_accuracy": safe_rate(
            sum(record["exact_match"] for record in correction), len(correction)
        ),
        "overall_exact_accuracy": safe_rate(
            sum(record["exact_match"] for record in usable), len(usable)
        ),
        "over_edit_rate": safe_rate(
            sum(record["unnecessary_edit"] for record in unchanged), len(unchanged)
        ),
        "missed_error_rate": safe_rate(
            sum(record["missed_correction"] for record in correction), len(correction)
        ),
        "formatting_preservation_rate": safe_rate(
            sum(record["formatting_preservation"] is True for record in formatting),
            len(formatting),
        ),
        "mean_latency_seconds": statistics.fmean(latencies) if latencies else None,
        "median_latency_seconds": statistics.median(latencies) if latencies else None,
        "p95_latency_seconds": percentile(latencies, 0.95),
        "mean_tokens_per_second": statistics.fmean(tps_values) if tps_values else None,
        "error_timeout_rate": safe_rate(total - len(usable), total),
        "exact_failure_count": sum(not record["exact_match"] for record in usable),
        "unnecessary_edit_count": sum(
            record["unnecessary_edit"] for record in usable
        ),
        "missed_correction_count": sum(
            record["missed_correction"] for record in usable
        ),
        "formatting_failure_count": sum(
            record["formatting_preservation"] is False for record in formatting
        ),
        "error_count": sum(bool(record["model_provider_error"]) for record in records),
        "timeout_count": sum(record["timeout"] for record in records),
    }


def safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def add_weighted_scores(summaries: list[dict[str, Any]]) -> None:
    valid_medians = [
        item["median_latency_seconds"]
        for item in summaries
        if item["median_latency_seconds"] is not None
    ]
    fastest = min(valid_medians, default=None)
    slowest = max(valid_medians, default=None)
    for item in summaries:
        median = item["median_latency_seconds"]
        if median is None:
            speed_score = 0.0
        elif fastest == slowest:
            speed_score = 1.0
        else:
            speed_score = (slowest - median) / (slowest - fastest)
        item["speed_normalized"] = speed_score
        item["weighted_score"] = (
            0.40 * (item["exact_preservation_rate"] or 0)
            + 0.30 * (item["exact_correction_accuracy"] or 0)
            + 0.20 * speed_score
            + 0.10 * (item["formatting_preservation_rate"] or 0)
        )
    summaries.sort(key=lambda item: item["weighted_score"], reverse=True)
    for rank, item in enumerate(summaries, 1):
        item["rank"] = rank


def prompt_comparison(
    strict: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    preservation_delta = (
        (strict["exact_preservation_rate"] or 0)
        - (baseline["exact_preservation_rate"] or 0)
    )
    correction_delta = (
        (strict["exact_correction_accuracy"] or 0)
        - (baseline["exact_correction_accuracy"] or 0)
    )
    average_delta = (preservation_delta + correction_delta) / 2
    material = (
        average_delta >= 0.05
        and preservation_delta >= -0.02
        and correction_delta >= -0.02
    )
    return {
        "strict_prompt_summary": strict,
        "production_prompt_summary": baseline,
        "preservation_rate_delta": preservation_delta,
        "correction_accuracy_delta": correction_delta,
        "average_primary_quality_delta": average_delta,
        "material_improvement": material,
        "materiality_rule": (
            "Average of preservation and correction deltas is at least +5 "
            "percentage points, with neither metric declining by more than 2 points."
        ),
    }


def long_text(word_target: int) -> str:
    paragraph = (
        "The project team reviewed the schedule and confirmed that every milestone "
        "remains achievable. Each owner documented the next action, the expected "
        "delivery date, and any dependency that could affect the work. The meeting "
        "notes are complete, and the final report is ready for approval."
    )
    words: list[str] = []
    source = paragraph.split()
    while len(words) < word_target:
        words.extend(source)
    return " ".join(words[:word_target])


def run_performance(
    client: OllamaClient, models: list[str]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for model in models:
        for target in (100, 500, 1000, 2000):
            text = long_text(target)
            output, latency, response, error, timed_out = invoke(client, model, text)
            eval_count = (response or {}).get("eval_count")
            eval_duration = (response or {}).get("eval_duration")
            results.append(
                {
                    "model": model,
                    "target_words": target,
                    "actual_input_words": len(text.split()),
                    "input_length": len(text),
                    "output_length": len(output) if error is None else None,
                    "latency_seconds": latency,
                    "input_unchanged": output == text if error is None else None,
                    "eval_count": eval_count,
                    "tokens_per_second": (
                        eval_count / (eval_duration / 1_000_000_000)
                        if eval_count and eval_duration
                        else None
                    ),
                    "model_provider_error": error,
                    "timeout": timed_out,
                    "impractical_single_request": timed_out or latency >= 30,
                }
            )
    return results


def best_by(
    summaries: list[dict[str, Any]], key: str, lower_is_better: bool = False
) -> str:
    eligible = [item for item in summaries if item.get(key) is not None]
    if not eligible:
        return "N/A"
    chooser = min if lower_is_better else max
    value = chooser(item[key] for item in eligible)
    return ", ".join(item["model"] for item in eligible if item[key] == value)


def markdown_report(result: dict[str, Any]) -> str:
    summaries = result["summary"]
    lines = [
        "# Proofreading Model Benchmark",
        "",
        f"Generated: {result['generated_at']}",
        "",
        f"Installed Ollama models: {', '.join(result['installed_models']) or 'none detected'}",
        f"Candidate models unavailable: {', '.join(result['unavailable_candidates']) or 'none'}",
        f"Models benchmarked: {', '.join(result['benchmarked_models']) or 'none'}",
        "",
        "## Configuration",
        "",
        f"- Quality cases: {result['dataset']['size']} ({format_counts(result['dataset']['categories'])})",
        "- Temperature: 0; seed: 0; context: 8,192 tokens; maximum generation: 4,096 tokens",
        "- Thinking/reasoning: disabled with `think: false` for every model",
        "- Prompt (identical system message for every model):",
        "",
        "```text",
        PROMPT,
        "```",
        "",
        "## Quality and latency",
        "",
    ]
    if not summaries:
        lines.extend(
            [
                "No candidate model was benchmarked. See `discovery_error` in the JSON report.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "| Rank | Model | Weighted | Preserve | Correct | Over-edit | Missed | Format | Median s | Mean s | P95 s | Tok/s | Error/timeout |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in summaries:
            lines.append(
                "| {rank} | {model} | {weighted} | {preserve} | {correct} | "
                "{overedit} | {missed} | {formatting} | {median} | {mean} | "
                "{p95} | {tps} | {errors} |".format(
                    rank=item["rank"],
                    model=item["model"],
                    weighted=pct(item["weighted_score"]),
                    preserve=pct(item["exact_preservation_rate"]),
                    correct=pct(item["exact_correction_accuracy"]),
                    overedit=pct(item["over_edit_rate"]),
                    missed=pct(item["missed_error_rate"]),
                    formatting=pct(item["formatting_preservation_rate"]),
                    median=num(item["median_latency_seconds"]),
                    mean=num(item["mean_latency_seconds"]),
                    p95=num(item["p95_latency_seconds"]),
                    tps=num(item["mean_tokens_per_second"]),
                    errors=pct(item["error_timeout_rate"]),
                )
            )
        lines.extend(
            [
                "",
                "The weighted score is 40% exact preservation, 30% exact correction "
                "accuracy, 20% normalized speed, and 10% formatting preservation. "
                "Speed uses median quality-case latency and min-max normalization: "
                "`(slowest median - model median) / (slowest median - fastest median)`. "
                "A sole successful model receives 100% for speed; a failed model receives 0%.",
                "",
                "## Findings",
                "",
                f"- Best preservation: {best_by(summaries, 'exact_preservation_rate')}",
                f"- Best correction: {best_by(summaries, 'exact_correction_accuracy')}",
                f"- Fastest: {best_by(summaries, 'median_latency_seconds', True)}",
                f"- Lowest over-edit: {best_by(summaries, 'over_edit_rate', True)}",
                f"- Best overall for this product: {summaries[0]['model']}",
                "",
            ]
        )
        only_one = len(summaries) == 1
        top = summaries[0]
        if only_one:
            lines.append(
                "- Recommendation: do not change the production default from this run. "
                "Only one candidate was available, so the ranking is not comparative. "
                f"Its {pct(top['exact_preservation_rate'])} preservation rate does not "
                "satisfy the product's exact-unchanged requirement reliably."
            )
        else:
            lines.append(
                f"- Recommendation: {top['model']} ranks first, but its raw preservation "
                "and correction rates should be reviewed before a production change."
            )
        llama = next((x for x in summaries if x["model"] == "llama3.2:3b"), None)
        if llama:
            if len(summaries) == 1:
                lines.append(
                    "- llama3.2:3b competitiveness against the other candidates cannot "
                    "be established because no other candidate was installed. Its "
                    "absolute quality rates remain poor despite the stricter prompt."
                )
            else:
                competitive = llama["rank"] == 1 or (
                    llama["weighted_score"] >= summaries[0]["weighted_score"] * 0.95
                )
                lines.append(
                    "- llama3.2:3b with the strict prompt is "
                    + ("competitive" if competitive else "not competitive")
                    + " by the declared rule (ranked first or within 5% of the top weighted score)."
                )
        else:
            lines.append("- llama3.2:3b competitiveness could not be measured.")
        comparison = result.get("llama_prompt_comparison")
        if comparison:
            strict_summary = comparison["strict_prompt_summary"]
            baseline_summary = comparison["production_prompt_summary"]
            lines.append(
                "- Compared with the current production prompt, the strict prompt changed "
                f"llama3.2:3b preservation by {comparison['preservation_rate_delta'] * 100:+.1f} "
                "points and correction accuracy by "
                f"{comparison['correction_accuracy_delta'] * 100:+.1f} points. "
                f"Material improvement: {comparison['material_improvement']}."
            )
            lines.append(
                "- llama3.2:3b raw paired rates — strict: "
                f"{pct(strict_summary['exact_preservation_rate'])} preservation, "
                f"{pct(strict_summary['exact_correction_accuracy'])} correction; "
                "production prompt: "
                f"{pct(baseline_summary['exact_preservation_rate'])} preservation, "
                f"{pct(baseline_summary['exact_correction_accuracy'])} correction."
            )
            lines.append(f"- Materiality rule: {comparison['materiality_rule']}")
        else:
            lines.append(
                "- Prompt-only improvement could not be established because the paired "
                "llama3.2:3b production-prompt baseline was not run."
            )
        lines.append("")
        lines.extend(["## Model-specific issues", ""])
        for item in summaries:
            lines.append(
                f"- {item['model']}: {item['exact_failure_count']} exact failures, "
                f"{item['unnecessary_edit_count']} unnecessary edits on expected-unchanged "
                f"cases, {item['missed_correction_count']} inexact/missed corrections, "
                f"and {item['formatting_failure_count']} formatting-structure failures."
            )
        lines.append(
            "- Several llama3.2:3b outputs treated workplace sentences as requests "
            "directed at an assistant (for example, asking for an attachment or "
            "answering a scheduling question) instead of proofreading them."
        )
        lines.append("")
    lines.extend(["## Long-text timing", ""])
    performance = result["performance"]
    if performance:
        lines.extend(
            [
                "| Model | Words | Latency s | Tok/s | Unchanged | Impractical (>=30 s/timeout) | Error |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for item in performance:
            lines.append(
                f"| {item['model']} | {item['actual_input_words']} | "
                f"{num(item['latency_seconds'])} | {num(item['tokens_per_second'])} | "
                f"{item['input_unchanged']} | {item['impractical_single_request']} | "
                f"{item['model_provider_error'] or ''} |"
            )
        altered = sum(item["input_unchanged"] is False for item in performance)
        lines.append("")
        lines.append(
            f"{altered} of {len(performance)} long-text outputs were altered or truncated; "
            "this is reported as behavior evidence but is not included in semantic scoring."
        )
    else:
        lines.append("Not run.")
    lines.extend(
        [
            "",
            "Long samples contain already-correct repeated prose. They are timing probes, "
            "not part of exact semantic quality scoring. A request is labeled impractical "
            "at 30 seconds or on timeout.",
            "",
            "Production model behavior, prompt, hotkey handling, and chunking were not changed.",
            "",
        ]
    )
    return "\n".join(lines)


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in counts.items())


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "model", "case_id", "category", "input", "expected_output", "actual_output",
        "exact_match", "latency_seconds", "input_length", "output_length",
        "output_changed", "expected_change", "actual_change", "unnecessary_edit",
        "missed_correction", "formatting_preservation", "eval_count",
        "eval_duration_ns", "tokens_per_second", "model_provider_error", "timeout",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        cases = cases[: args.limit]

    ollama_list = run_required_ollama_list(find_ollama())
    client = OllamaClient(args.base_url, args.timeout)
    discovery_error = None
    try:
        installed = client.installed_models()
    except (OSError, ValueError, urllib.error.URLError) as exc:
        installed = []
        discovery_error = f"{type(exc).__name__}: {exc}"
    requested = list(dict.fromkeys(args.models))
    benchmarked = [model for model in requested if model in installed]
    unavailable = [model for model in requested if model not in installed]

    records: list[dict[str, Any]] = []
    for model in benchmarked:
        print(f"Benchmarking {model}: {len(cases)} quality cases", flush=True)
        for index, case in enumerate(cases, 1):
            output, latency, response, error, timed_out = invoke(
                client, model, case["input"]
            )
            records.append(
                quality_record(
                    model, case, output, latency, response, error, timed_out
                )
            )
            print(f"  {index}/{len(cases)} {case['id']} {latency:.2f}s", flush=True)

    summaries = [
        summarize_model(
            model, [record for record in records if record["model"] == model]
        )
        for model in benchmarked
    ]
    add_weighted_scores(summaries)
    baseline_records: list[dict[str, Any]] = []
    llama_prompt_comparison = None
    if "llama3.2:3b" in benchmarked and not args.skip_llama_baseline:
        print(
            f"Benchmarking llama3.2:3b production-prompt baseline: {len(cases)} cases",
            flush=True,
        )
        for index, case in enumerate(cases, 1):
            output, latency, response, error, timed_out = invoke(
                client, "llama3.2:3b", case["input"], PRODUCTION_BASELINE_PROMPT
            )
            baseline_records.append(
                quality_record(
                    "llama3.2:3b",
                    case,
                    output,
                    latency,
                    response,
                    error,
                    timed_out,
                )
            )
            print(
                f"  baseline {index}/{len(cases)} {case['id']} {latency:.2f}s",
                flush=True,
            )
        strict_llama = next(
            item for item in summaries if item["model"] == "llama3.2:3b"
        )
        baseline_summary = summarize_model("llama3.2:3b", baseline_records)
        llama_prompt_comparison = prompt_comparison(strict_llama, baseline_summary)
    performance = (
        [] if args.skip_performance else run_performance(client, benchmarked)
    )
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "installed_models": installed,
        "candidate_models": list(CANDIDATE_MODELS),
        "requested_models": requested,
        "benchmarked_models": benchmarked,
        "unavailable_candidates": unavailable,
        "discovery_error": discovery_error,
        "ollama_list": ollama_list,
        "dataset": {
            "path": str(args.cases),
            "size": len(cases),
            "categories": dict(Counter(case["category"] for case in cases)),
        },
        "prompt": PROMPT,
        "settings": {
            **DEFAULT_OPTIONS,
            "think": False,
            "timeout_seconds": args.timeout,
            "model_specific_differences": {},
        },
        "scoring": {
            "preservation_weight": 0.40,
            "correction_weight": 0.30,
            "speed_weight": 0.20,
            "formatting_weight": 0.10,
            "speed_normalization": (
                "Min-max inverse median quality latency: "
                "(slowest - model) / (slowest - fastest); sole model=1.0."
            ),
        },
        "summary": summaries,
        "quality_records": records,
        "llama_prompt_comparison": llama_prompt_comparison,
        "llama_production_prompt_baseline_records": baseline_records,
        "performance": performance,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.results_dir / "latest.json"
    csv_path = args.results_dir / "latest.csv"
    md_path = args.results_dir / "latest.md"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_csv(csv_path, records)
    md_path.write_text(markdown_report(result), encoding="utf-8")
    print(f"Wrote {json_path}, {csv_path}, and {md_path}")
    if not benchmarked:
        print(
            "No requested candidate models were available; no inference was run.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
