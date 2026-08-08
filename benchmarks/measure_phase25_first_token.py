#!/usr/bin/env python3
"""Measure cold and warm first-token timing with v0.4.0 production settings."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.providers.ollama import (
    OLLAMA_API_URL,
    PROOFREADING_GENERATION_OPTIONS,
    PROOFREADING_KEEP_ALIVE,
    OllamaCliOfflineWritingProvider,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("results")
        / "phase25-v0.4.0-first-token.json",
    )
    return parser.parse_args()


def probe(model: str, text: str, timeout_seconds: float) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": True,
        "think": False,
        "keep_alive": PROOFREADING_KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": REVISION_INSTRUCTION},
            {"role": "user", "content": text},
        ],
        "options": PROOFREADING_GENERATION_OPTIONS,
    }
    request = urllib.request.Request(
        f"{OLLAMA_API_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_token_seconds: float | None = None
    pieces: list[str] = []
    final: dict[str, Any] = {}
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        for line in response:
            item = json.loads(line.decode("utf-8"))
            message = item.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content:
                if first_token_seconds is None:
                    first_token_seconds = time.perf_counter() - started
                pieces.append(content)
            final = item
            if item.get("done") is True:
                break
    return {
        "first_token_seconds": first_token_seconds,
        "wall_seconds": time.perf_counter() - started,
        "output": "".join(pieces),
        "load_duration_seconds": (final.get("load_duration") or 0) / 1e9,
        "prompt_eval_duration_seconds": (final.get("prompt_eval_duration") or 0)
        / 1e9,
        "generation_duration_seconds": (final.get("eval_duration") or 0) / 1e9,
        "prompt_token_count": final.get("prompt_eval_count"),
        "generation_token_count": final.get("eval_count"),
    }


def main() -> int:
    args = parse_args()
    config = OfflineWritingConfig()
    provider = OllamaCliOfflineWritingProvider(config.model, config.ollama_executable)
    provider.ensure_api_running(timeout_seconds=20.0)
    text = "I recieved the adress yesterday."
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": "v0.4.0",
        "model": config.model,
        "input": text,
        "cold": probe(config.model, text, config.timeout_seconds),
        "warm": probe(config.model, text, config.timeout_seconds),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
