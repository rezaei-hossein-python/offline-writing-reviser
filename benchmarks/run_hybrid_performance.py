#!/usr/bin/env python3
"""Focused cold/warm Ollama experiments for the Phase 18D hybrid benchmark."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run_hybrid_benchmark import (
    HYBRID_PROMPT,
    MODEL,
    build_gemma_instruction,
    validate_gemma_output,
)


COMPACT_PROMPT = """Correct only objective grammar or spelling errors supported by the advisory evidence.
Make the fewest possible edits. Do not rewrite style, tone, meaning, or facts.
Preserve capitalization, punctuation, spacing, typography, paragraphs, line breaks, lists, and formatting.
If no correction is needed, return the input exactly. Return text only."""

MINIMAL_PROMPT = """Correct only objective grammar or spelling errors near the advisory spans.
Use minimum edits; preserve meaning and all formatting. No style changes or paraphrasing.
If no correction is needed, copy the input exactly. Output text only."""

STATIC_PROMPT = """Correct only objective grammar or spelling errors supported by the advisory evidence.
Use minimum edits. Preserve meaning, punctuation, spacing, line breaks, and formatting.
Do not rewrite style or tone. If no correction is needed, copy the text exactly.
Output only the corrected text."""

ULTRA_PROMPT = """Fix only genuine grammar or spelling errors supported by the evidence.
Use minimum edits. Preserve meaning, punctuation, spacing, line breaks, and formatting.
No style changes or paraphrasing. If no error exists, copy the text exactly.
Output corrected text only."""

QUALITY_COMPACT_PROMPT = """Correct only genuine objective grammar or spelling errors supported by the evidence and context.
Make minimum changes. Use time markers and sentence context for tense and inflection.
Correct subject-verb agreement and verb-form errors using that context.
Do not paraphrase, improve style or tone, change meaning or facts, expand contractions, or rewrite unless grammar requires it.
Preserve capitalization, punctuation, spacing, typography, paragraphs, line breaks, lists, and formatting.
If no correction is needed, copy the text exactly. Output text only."""


def compact_instruction(
    evidence: list[dict[str, Any]], prompt: str = COMPACT_PROMPT
) -> str:
    lines = [prompt, "", "Advisory evidence:"]
    for item in evidence:
        replacements = "|".join(item["replacement_candidates"])
        lines.append(
            f"{item['rule_id']} @{item['offset']}:{item['offset'] + item['length']} "
            f"{item['original_text']!r} -> {replacements}"
        )
    return "\n".join(lines)


def full_prompt_compact_evidence(evidence: list[dict[str, Any]]) -> str:
    lines = [HYBRID_PROMPT, "", "Unresolved LanguageTool evidence:"]
    for item in evidence:
        replacements = "|".join(item["replacement_candidates"])
        lines.append(
            f"{item['rule_id']} @{item['offset']}:{item['offset'] + item['length']} "
            f"{item['original_text']!r} -> {replacements}"
        )
    return "\n".join(lines)


def full_evidence_instruction(
    evidence: list[dict[str, Any]], prompt: str
) -> str:
    original = build_gemma_instruction(evidence)
    return prompt + original[len(HYBRID_PROMPT) :]


def plain_user(text: str, evidence: list[dict[str, Any]]) -> str:
    return text


def structured_user(text: str, evidence: list[dict[str, Any]]) -> str:
    lines = ["Advisory evidence:"]
    for item in evidence:
        replacements = "|".join(item["replacement_candidates"])
        lines.append(
            f"{item['rule_id']} @{item['offset']}:{item['offset'] + item['length']} "
            f"{item['original_text']!r} -> {replacements}"
        )
    lines.extend(["Text:", text])
    return "\n".join(lines)


def text_only_user(text: str, evidence: list[dict[str, Any]]) -> str:
    return f"Text:\n{text}"


@dataclass(frozen=True)
class Experiment:
    name: str
    instruction_builder: Callable[[list[dict[str, Any]]], str]
    user_builder: Callable[[str, list[dict[str, Any]]], str]
    num_ctx: int
    num_predict: int
    keep_alive: str = "10m"
    num_thread: int | None = None
    split_system_messages: bool = False
    raw_prompt: bool = False


EXPERIMENTS = {
    item.name: item
    for item in (
        Experiment("baseline_18d", build_gemma_instruction, plain_user, 8192, 4096),
        Experiment(
            "baseline_small_limits",
            build_gemma_instruction,
            plain_user,
            2048,
            128,
        ),
        Experiment(
            "compact_prompt", compact_instruction, plain_user, 8192, 4096
        ),
        Experiment(
            "compact_small_limits",
            compact_instruction,
            plain_user,
            2048,
            128,
        ),
        Experiment(
            "minimal_ctx1024",
            lambda evidence: compact_instruction(evidence, MINIMAL_PROMPT),
            plain_user,
            1024,
            128,
        ),
        Experiment(
            "static_prefix_ctx1024",
            lambda evidence: STATIC_PROMPT,
            structured_user,
            1024,
            128,
        ),
        Experiment(
            "static_prefix_ctx512",
            lambda evidence: STATIC_PROMPT,
            structured_user,
            512,
            128,
        ),
        Experiment(
            "static_text_only_ctx512",
            lambda evidence: STATIC_PROMPT,
            text_only_user,
            512,
            128,
        ),
        Experiment(
            "ultra_static_ctx1024",
            lambda evidence: ULTRA_PROMPT,
            structured_user,
            1024,
            128,
        ),
        Experiment(
            "ultra_static_threads6",
            lambda evidence: ULTRA_PROMPT,
            structured_user,
            1024,
            128,
            num_thread=6,
        ),
        Experiment(
            "ultra_static_threads12",
            lambda evidence: ULTRA_PROMPT,
            structured_user,
            1024,
            128,
            num_thread=12,
        ),
        Experiment(
            "quality_static_ctx1024",
            lambda evidence: HYBRID_PROMPT,
            structured_user,
            1024,
            128,
        ),
        Experiment(
            "quality_compact_static_ctx1024",
            lambda evidence: QUALITY_COMPACT_PROMPT,
            structured_user,
            1024,
            128,
        ),
        Experiment(
            "full_compact_evidence_ctx1024",
            full_prompt_compact_evidence,
            plain_user,
            1024,
            128,
        ),
        Experiment(
            "split_system_ctx1024",
            build_gemma_instruction,
            plain_user,
            1024,
            128,
            split_system_messages=True,
        ),
        Experiment(
            "raw_exact_ctx2048",
            build_gemma_instruction,
            plain_user,
            2048,
            128,
            raw_prompt=True,
        ),
        Experiment(
            "quality_compact_combined",
            lambda evidence: full_evidence_instruction(
                evidence, QUALITY_COMPACT_PROMPT
            ),
            plain_user,
            2048,
            128,
        ),
    )
}


class TelemetryClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Ollama returned a non-object response.")
        return parsed

    def unload(self, model: str) -> None:
        self.request(
            "/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        )

    def chat(
        self,
        model: str,
        text: str,
        instruction: str,
        experiment: Experiment,
    ) -> tuple[dict[str, Any], float, int]:
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ]
        marker = "\n\nUnresolved LanguageTool evidence:"
        if experiment.split_system_messages:
            base_prompt, evidence_text = instruction.split(marker, 1)
            messages = [
                {"role": "system", "content": base_prompt},
                {
                    "role": "system",
                    "content": "Unresolved LanguageTool evidence:" + evidence_text,
                },
                {"role": "user", "content": text},
            ]
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": experiment.keep_alive,
            "messages": messages,
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": experiment.num_ctx,
                "num_predict": experiment.num_predict,
            },
        }
        if experiment.num_thread is not None:
            payload["options"]["num_thread"] = experiment.num_thread
        path = "/api/chat"
        if experiment.raw_prompt:
            # This is the exact token layout produced by Gemma 3's installed
            # Ollama chat template for the two messages above.
            payload.pop("messages")
            payload["raw"] = True
            payload["prompt"] = (
                f"<start_of_turn>user\n{instruction}<end_of_turn>\n"
                f"<start_of_turn>user\n{text}<end_of_turn>\n"
                "<start_of_turn>model\n"
            )
            path = "/api/generate"
        encoded_size = len(json.dumps(payload).encode("utf-8"))
        started = time.perf_counter()
        response = self.request(path, payload)
        return response, time.perf_counter() - started, encoded_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare cold/warm hybrid Gemma configurations with telemetry."
    )
    parser.add_argument(
        "--hybrid-results",
        type=Path,
        default=Path(__file__).with_name("results") / "hybrid" / "latest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).with_name("results")
            / "hybrid"
            / "performance_experiments.json"
        ),
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        choices=sorted(EXPERIMENTS),
        default=list(EXPERIMENTS),
    )
    parser.add_argument("--limit-routed", type=int, default=5)
    parser.add_argument(
        "--case-ids",
        nargs="+",
        help="Optional routed case IDs; overrides --limit-routed.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--skip-cold",
        action="store_true",
        help="Do not unload the model before each configuration's first request.",
    )
    return parser.parse_args()


def response_text(response: dict[str, Any]) -> str:
    generated = response.get("response")
    if isinstance(generated, str):
        return generated
    message = response.get("message")
    value = message.get("content") if isinstance(message, dict) else None
    if not isinstance(value, str):
        raise ValueError("Ollama chat response is missing message.content.")
    return value


def telemetry_record(
    case: dict[str, Any],
    experiment: Experiment,
    response: dict[str, Any],
    wall_seconds: float,
    payload_bytes: int,
    cold: bool,
) -> dict[str, Any]:
    output = response_text(response)
    validation = validate_gemma_output(
        case["safe_output"], output, case["routing_evidence"]
    )
    final_output = output if validation["accepted"] else case["safe_output"]
    return {
        "configuration": experiment.name,
        "case_id": case["case_id"],
        "cold": cold,
        "wall_seconds": wall_seconds,
        "payload_bytes": payload_bytes,
        "response_bytes": len(json.dumps(response).encode("utf-8")),
        "total_duration_seconds": response.get("total_duration", 0) / 1e9,
        "load_duration_seconds": response.get("load_duration", 0) / 1e9,
        "prompt_eval_count": response.get("prompt_eval_count"),
        "prompt_eval_duration_seconds": (
            response.get("prompt_eval_duration", 0) / 1e9
        ),
        "eval_count": response.get("eval_count"),
        "eval_duration_seconds": response.get("eval_duration", 0) / 1e9,
        "output": output,
        "validation_accepted": validation["accepted"],
        "validation_rejection_reasons": validation["rejection_reasons"],
        "final_exact_match": final_output == case["expected"],
    }


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]

    summaries: list[dict[str, Any]] = []
    for name in dict.fromkeys(record["configuration"] for record in records):
        selected = [record for record in records if record["configuration"] == name]
        warm = [record for record in selected if not record["cold"]]
        cold = [record for record in selected if record["cold"]]
        # The first warm request repeats the cold case exactly and can receive
        # an unusually favorable whole-prompt cache hit. Report it, but keep a
        # separate steady-state series that excludes that artificial repeat.
        steady = warm[1:] if cold and len(warm) > 1 else warm
        warm_walls = [item["wall_seconds"] for item in warm]
        steady_walls = [item["wall_seconds"] for item in steady]
        summaries.append(
            {
                "configuration": name,
                "cold_wall_seconds": (
                    cold[0]["wall_seconds"] if cold else None
                ),
                "cold_load_seconds": (
                    cold[0]["load_duration_seconds"] if cold else None
                ),
                "warm_mean_wall_seconds": (
                    statistics.fmean(warm_walls)
                    if warm
                    else None
                ),
                "warm_median_wall_seconds": (
                    statistics.median(warm_walls)
                    if warm
                    else None
                ),
                "warm_p95_wall_seconds": p95(warm_walls),
                "steady_request_count": len(steady),
                "steady_mean_wall_seconds": (
                    statistics.fmean(steady_walls) if steady else None
                ),
                "steady_median_wall_seconds": (
                    statistics.median(steady_walls) if steady else None
                ),
                "steady_p95_wall_seconds": p95(steady_walls),
                "warm_mean_prompt_eval_seconds": (
                    statistics.fmean(
                        item["prompt_eval_duration_seconds"] for item in warm
                    )
                    if warm
                    else None
                ),
                "warm_mean_eval_seconds": (
                    statistics.fmean(item["eval_duration_seconds"] for item in warm)
                    if warm
                    else None
                ),
                "warm_mean_prompt_tokens": (
                    statistics.fmean(item["prompt_eval_count"] for item in warm)
                    if warm
                    else None
                ),
                "warm_mean_output_tokens": (
                    statistics.fmean(item["eval_count"] for item in warm)
                    if warm
                    else None
                ),
                "accepted_count": sum(
                    item["validation_accepted"] for item in selected
                ),
                "exact_count": sum(item["final_exact_match"] for item in selected),
                "record_count": len(selected),
            }
        )
    return summaries


def main() -> int:
    args = parse_args()
    if args.limit_routed < 1:
        raise ValueError("--limit-routed must be positive")
    result = json.loads(args.hybrid_results.read_text(encoding="utf-8"))
    all_routed = [
        record
        for record in result["case_records"]
        if record["routing_decision"]
    ]
    if args.case_ids:
        by_id = {record["case_id"]: record for record in all_routed}
        missing = [case_id for case_id in args.case_ids if case_id not in by_id]
        if missing:
            raise ValueError(f"Routed case IDs not found: {missing}")
        routed = [by_id[case_id] for case_id in args.case_ids]
    else:
        routed = all_routed[: args.limit_routed]
    if not routed:
        raise ValueError("No routed hybrid cases were found.")
    client = TelemetryClient(args.base_url, args.timeout)
    records: list[dict[str, Any]] = []
    for config_name in args.configs:
        experiment = EXPERIMENTS[config_name]
        if not args.skip_cold:
            client.unload(args.model)
            response, wall, payload_bytes = client.chat(
                args.model,
                experiment.user_builder(
                    routed[0]["safe_output"], routed[0]["routing_evidence"]
                ),
                experiment.instruction_builder(routed[0]["routing_evidence"]),
                experiment,
            )
            record = telemetry_record(
                routed[0], experiment, response, wall, payload_bytes, True
            )
            records.append(record)
            print(
                f"{config_name} cold {wall:.3f}s "
                f"load={record['load_duration_seconds']:.3f}s "
                f"prompt={record['prompt_eval_duration_seconds']:.3f}s",
                flush=True,
            )
        for case in routed:
            response, wall, payload_bytes = client.chat(
                args.model,
                experiment.user_builder(
                    case["safe_output"], case["routing_evidence"]
                ),
                experiment.instruction_builder(case["routing_evidence"]),
                experiment,
            )
            record = telemetry_record(
                case, experiment, response, wall, payload_bytes, False
            )
            records.append(record)
            print(
                f"{config_name} warm {case['case_id']} {wall:.3f}s "
                f"prompt={record['prompt_eval_duration_seconds']:.3f}s "
                f"eval={record['eval_duration_seconds']:.3f}s "
                f"exact={record['final_exact_match']}",
                flush=True,
            )
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "source_hybrid_results": str(args.hybrid_results),
        "selected_case_ids": [case["case_id"] for case in routed],
        "configurations": {
            name: {
                "num_ctx": EXPERIMENTS[name].num_ctx,
                "num_predict": EXPERIMENTS[name].num_predict,
                "keep_alive": EXPERIMENTS[name].keep_alive,
                "num_thread": EXPERIMENTS[name].num_thread,
                "split_system_messages": EXPERIMENTS[
                    name
                ].split_system_messages,
            }
            for name in args.configs
        },
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
