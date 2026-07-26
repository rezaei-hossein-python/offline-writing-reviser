from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BENCHMARKS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hybrid = load_module("hybrid_benchmark_phase18e", "run_hybrid_benchmark.py")
performance = load_module(
    "hybrid_performance_phase18e", "run_hybrid_performance.py"
)


def test_resident_client_preserves_phase18d_request_and_adds_keep_alive():
    client = hybrid.ResidentOllamaClient(
        "http://127.0.0.1:11434", 30.0, "10m"
    )
    requests: list[tuple[str, dict]] = []

    def fake_request(path: str, payload: dict):
        requests.append((path, payload))
        if path == "/api/generate":
            return {"done": True}
        return {
            "message": {"content": "She works."},
            "total_duration": 10,
            "load_duration": 2,
            "prompt_eval_count": 3,
            "prompt_eval_duration": 4,
            "eval_count": 2,
            "eval_duration": 1,
        }

    client.request = fake_request
    client.unload("gemma3:4b")
    output, response = client.generate(
        "gemma3:4b", "She work.", "Proofread conservatively."
    )

    assert output == "She works."
    assert requests[0][1]["keep_alive"] == 0
    _, payload = requests[1]
    assert payload["keep_alive"] == "10m"
    assert payload["messages"] == [
        {"role": "system", "content": "Proofread conservatively."},
        {"role": "user", "content": "She work."},
    ]
    assert payload["options"] == hybrid.DEFAULT_OPTIONS
    assert response["_benchmark_cold_start"] is True
    assert response["_benchmark_request_payload_bytes"] > 0
    assert response["_benchmark_response_bytes"] > 0


def test_resident_client_marks_only_first_request_cold():
    client = hybrid.ResidentOllamaClient("http://localhost", 30.0, "10m")
    client.request = lambda path, payload: {
        "message": {"content": "unchanged"}
    }
    client.cold_start_pending = True

    _, first = client.generate("gemma3:4b", "unchanged", "instruction")
    _, second = client.generate("gemma3:4b", "unchanged", "instruction")

    assert first["_benchmark_cold_start"] is True
    assert second["_benchmark_cold_start"] is False


def test_performance_summary_separates_cold_warm_and_repeated_cache_probe():
    def record(cold: bool, wall: float) -> dict:
        return {
            "configuration": "candidate",
            "cold": cold,
            "wall_seconds": wall,
            "load_duration_seconds": 5.0 if cold else 0.0,
            "prompt_eval_duration_seconds": 1.0,
            "eval_duration_seconds": 0.5,
            "prompt_eval_count": 100,
            "eval_count": 5,
            "validation_accepted": True,
            "final_exact_match": True,
        }

    summary = performance.summarize(
        [
            record(True, 12.0),
            record(False, 2.0),  # exact repeat of the cold probe
            record(False, 7.0),
            record(False, 9.0),
        ]
    )[0]

    assert summary["cold_wall_seconds"] == 12.0
    assert summary["warm_median_wall_seconds"] == 7.0
    assert summary["warm_p95_wall_seconds"] == 9.0
    assert summary["steady_request_count"] == 2
    assert summary["steady_mean_wall_seconds"] == 8.0
    assert summary["steady_median_wall_seconds"] == 8.0
    assert summary["steady_p95_wall_seconds"] == 9.0


def test_raw_response_text_is_supported_for_exact_template_experiments():
    assert performance.response_text({"response": "Corrected text"}) == (
        "Corrected text"
    )


def test_experiment_prompts_do_not_contain_benchmark_case_ids():
    rendered = "\n".join(
        experiment.instruction_builder([])
        for experiment in performance.EXPERIMENTS.values()
    )
    assert "grammar-" not in rendered
    assert "mixed-" not in rendered
