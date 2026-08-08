from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from offline_writing_reviser.config import OfflineWritingConfig


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "run_lightweight_paraphrasing_evaluation.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("lightweight_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_corpus_is_balanced_and_synthetic():
    payload = json.loads(
        (ROOT / "benchmarks" / "phase25_paraphrasing_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases = payload["cases"]
    categories = {case["category"] for case in cases}
    assert len(cases) >= 23
    assert len(categories) >= 20
    assert {case["benefit"] for case in cases} >= {
        "unchanged",
        "paraphrase",
        "conservative",
    }


def test_prompt_and_settings_are_narrow_and_comparable():
    benchmark = load_benchmark()
    lowered = benchmark.PROMPT.casefold()
    assert "clear, natural, fluent english" in lowered
    assert "already natural and well written" in lowered
    assert "no reasoning" in lowered
    assert "diagnose grammar" not in lowered
    assert benchmark.OPTIONS["num_ctx"] == 4096
    assert benchmark.OPTIONS["num_predict"] <= 512
    assert benchmark.MODELS == ("gemma3:1b", "qwen3:1.7b", "llama3.2:1b")


def test_output_evaluation_compares_semantics_with_original_text():
    benchmark = load_benchmark()
    result = benchmark.evaluate_output(
        "Alex may approve 12 requests.",
        "Alex may approve 12 requests.",
        "Alex will approve 12 requests.",
    )
    assert result["validator_accepted"] is False
    assert "modality_not_preserved" in result["semantic_reasons"]


def test_percentile_is_deterministic():
    benchmark = load_benchmark()
    assert benchmark.percentile([1.0, 2.0, 3.0], 0.95) == 2.9


def test_long_finalist_runner_is_bounded_to_paragraph_chunks():
    source = (
        ROOT / "benchmarks" / "run_lightweight_long_finalist.py"
    ).read_text(encoding="utf-8")
    assert 'default="qwen3:1.7b"' in source
    assert 'fixture["paragraphs"]' in source
    assert '"model_request_count": len(records)' in source
    assert "gemma3:4b" not in source


def test_manual_review_classifies_every_output_once():
    cases = json.loads(
        (ROOT / "benchmarks" / "phase25_paraphrasing_cases.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    review = json.loads(
        (ROOT / "benchmarks" / "phase25_lightweight_manual_review.json").read_text(
            encoding="utf-8"
        )
    )
    expected_ids = {case["id"] for case in cases}
    allowed = set(review["allowed_primary_outcomes"])
    assert set(review["models"]) == {
        "gemma3:1b",
        "qwen3:1.7b",
        "llama3.2:1b",
    }
    for model in review["models"].values():
        assert set(model["classifications"]) == expected_ids
        assert set(model["classifications"].values()) <= allowed


def test_tracked_results_retain_required_pipeline_evidence():
    report = json.loads(
        (
            ROOT
            / "benchmarks"
            / "baselines"
            / "phase25-lightweight-model-evaluation.json"
        ).read_text(encoding="utf-8")
    )
    assert len(report["models"]) == 3
    for model in report["models"]:
        assert len(model["records"]) == report["case_count"]
        for record in model["records"]:
            assert isinstance(record["original_text"], str)
            assert isinstance(record["languagetool_text"], str)
            assert isinstance(record["candidate_output"], str)
            assert isinstance(record["evaluation"]["validator_accepted"], bool)
            assert "rejection_reason" in record["evaluation"]


def test_checkpoint_does_not_change_production_model():
    assert OfflineWritingConfig().model == "gemma3:4b"
