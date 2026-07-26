from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))
SCRIPT = BENCHMARKS / "run_hybrid_benchmark.py"
SPEC = importlib.util.spec_from_file_location("hybrid_benchmark", SCRIPT)
assert SPEC and SPEC.loader
hybrid = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hybrid
SPEC.loader.exec_module(hybrid)


def raw_match(
    offset: int,
    length: int,
    replacements: list[str],
    rule_id: str,
    category_id: str = "GRAMMAR",
    message: str = "Test evidence",
) -> dict:
    return {
        "offset": offset,
        "length": length,
        "message": message,
        "shortMessage": "Test",
        "replacements": [{"value": value} for value in replacements],
        "rule": {
            "id": rule_id,
            "description": "Test rule",
            "issueType": "grammar",
            "category": {"id": category_id, "name": category_id.title()},
        },
    }


def normalized(source: str, *matches: dict) -> list[dict]:
    return hybrid.normalize_matches({"matches": list(matches)}, source)


def route(source: str, safe_count: int, *matches: dict) -> dict:
    normalized_matches = normalized(source, *matches)
    _, decisions, _ = hybrid.safe_filter(source, normalized_matches)
    return hybrid.route_post_safe(normalized_matches, decisions, safe_count)


def evidence(
    source: str,
    offset: int,
    length: int,
    rule_id: str = "HE_VERB_AGR",
    category_id: str = "GRAMMAR",
) -> list[dict]:
    match_item = normalized(
        source,
        raw_match(offset, length, ["works"], rule_id, category_id),
    )[0]
    return [
        {
            "match_index": match_item["match_index"],
            "escalation_class": "ambiguous_grammar",
            "rule_id": rule_id,
            "category_id": category_id,
            "message": match_item["message"],
            "short_message": match_item["short_message"],
            "offset": offset,
            "length": length,
            "original_text": match_item["original_text"],
            "replacement_candidates": match_item["replacements"],
            "safe_rejection_reason": "ambiguous_rule",
        }
    ]


def test_clean_case_does_not_route_to_gemma():
    result = hybrid.route_post_safe([], [], 0)
    assert result == {
        "route_to_gemma": False,
        "reason": "clean_no_meaningful_evidence",
        "evidence": [],
    }


def test_safe_only_case_does_not_route_after_successful_correction():
    result = hybrid.route_post_safe([], [], 1)
    assert result["route_to_gemma"] is False
    assert result["reason"] == "safe_resolved_all_actionable_evidence"


def test_remaining_ambiguous_match_routes_to_gemma():
    source = "She finish today."
    result = route(
        source,
        0,
        raw_match(4, 6, ["finishes", "finished"], "HE_VERB_AGR"),
    )
    assert result["route_to_gemma"] is True
    assert result["reason"] == "remaining_ambiguous_grammar"


def test_ignore_only_evidence_does_not_route():
    source = "Please return it back."
    result = route(
        source,
        0,
        raw_match(17, 4, [""], "RETURN_BACK", "REDUNDANCY"),
    )
    assert result["route_to_gemma"] is False
    assert result["reason"] == "ignore_only_evidence"


def test_ambiguous_non_safe_spelling_rule_does_not_route():
    source = "The calender invitation arrived."
    result = route(
        source,
        0,
        raw_match(4, 8, ["calendar"], "CALENDER", "CONFUSED_WORDS"),
    )
    assert result["route_to_gemma"] is False
    assert result["reason"] == "non_escalatable_evidence"


def test_unresolved_spelling_candidates_route_for_context():
    source = "The employes waited."
    result = route(
        source,
        0,
        raw_match(
            4,
            8,
            ["employs", "employed", "employees"],
            "MORFOLOGIK_RULE_EN_US",
            "TYPOS",
        ),
    )
    assert result["route_to_gemma"] is True
    assert result["reason"] == "remaining_contextual_spelling"


def test_mixed_safe_partial_case_routes_when_grammar_remains():
    source = "He don't have the necessary permit."
    result = route(
        source,
        1,
        raw_match(3, 5, ["doesn't", "didn't"], "HE_VERB_AGR"),
    )
    assert result["route_to_gemma"] is True
    assert result["reason"] == "safe_partial_with_unresolved_grammar"


def test_gemma_exact_local_correction_is_accepted():
    source = "She work today."
    result = hybrid.validate_gemma_output(
        source, "She works today.", evidence(source, 4, 4)
    )
    assert result["accepted"] is True
    assert result["rejection_reasons"] == []


def test_unchanged_gemma_output_is_accepted_safely():
    source = "She works today."
    result = hybrid.validate_gemma_output(
        source, source, evidence(source, 4, 5)
    )
    assert result["accepted"] is True
    assert result["changed_opcodes"] == []


def test_broad_rewrite_is_rejected():
    source = "She work today because the report is due."
    output = "Today, she should complete her employment duties promptly."
    result = hybrid.validate_gemma_output(
        source, output, evidence(source, 4, 4)
    )
    assert result["accepted"] is False
    assert "excessive_character_changes" in result["rejection_reasons"]


def test_unrelated_sentence_rewrite_is_rejected():
    source = "She work today. The sky is blue."
    output = "She works today. Clouds cover the horizon."
    result = hybrid.validate_gemma_output(
        source, output, evidence(source, 4, 4)
    )
    assert result["accepted"] is False
    assert "edit_outside_evidence_window" in result["rejection_reasons"]


def test_formatting_damage_is_rejected():
    source = "- She work today.\n- Keep this line."
    output = "She works today.\n- Keep this line."
    result = hybrid.validate_gemma_output(
        source, output, evidence(source, 6, 4)
    )
    assert result["accepted"] is False
    assert "formatting_structure_changed" in result["rejection_reasons"]


def test_newline_damage_is_rejected():
    source = "She work today.\nKeep this line."
    output = "She works today. Keep this line."
    result = hybrid.validate_gemma_output(
        source, output, evidence(source, 4, 4)
    )
    assert result["accepted"] is False
    assert "newline_structure_changed" in result["rejection_reasons"]


def test_empty_result_is_rejected():
    result = hybrid.validate_gemma_output("She work.", "", evidence("She work.", 4, 4))
    assert result["accepted"] is False
    assert "empty_output" in result["rejection_reasons"]


def test_commentary_result_is_rejected():
    source = "She work today."
    result = hybrid.validate_gemma_output(
        source,
        "Corrected text: She works today.",
        evidence(source, 4, 4),
    )
    assert result["accepted"] is False
    assert "commentary_or_label" in result["rejection_reasons"]


def test_excessive_length_change_is_rejected():
    source = "She work today."
    output = "She works today and then completes many unrelated additional tasks."
    result = hybrid.validate_gemma_output(
        source, output, evidence(source, 4, 4)
    )
    assert result["accepted"] is False
    assert "extreme_length_change" in result["rejection_reasons"]


def test_edit_near_language_tool_evidence_is_accepted():
    source = "They was waiting."
    result = hybrid.validate_gemma_output(
        source, "They were waiting.", evidence(source, 5, 3)
    )
    assert result["accepted"] is True


def test_multiple_local_edits_are_accepted():
    source = "She work and he walk."
    output = "She works and he walks."
    items = evidence(source, 4, 4) + evidence(source, 16, 4)
    result = hybrid.validate_gemma_output(source, output, items)
    assert result["accepted"] is True
    assert len(result["changed_opcodes"]) == 2


def test_ambiguous_spelling_candidate_without_context_resolution_is_rejected():
    source = "The employes is waiting."
    item = evidence(
        source,
        4,
        8,
        "MORFOLOGIK_RULE_EN_US",
        "TYPOS",
    )[0]
    item["escalation_class"] = "contextual_spelling"
    item["replacement_candidates"] = ["employs", "employed", "employees"]
    result = hybrid.validate_gemma_output(
        source, "The employees is waiting.", [item]
    )
    assert result["accepted"] is False
    assert (
        "ambiguous_spelling_candidate_without_contextual_resolution"
        in result["rejection_reasons"]
    )


def test_contextual_spelling_plus_local_grammar_resolution_is_accepted():
    source = "The employes is waiting."
    item = evidence(
        source,
        4,
        8,
        "MORFOLOGIK_RULE_EN_US",
        "TYPOS",
    )[0]
    item["escalation_class"] = "contextual_spelling"
    item["replacement_candidates"] = ["employs", "employed", "employees"]
    result = hybrid.validate_gemma_output(
        source, "The employees are waiting.", [item]
    )
    assert result["accepted"] is True


class FakeLanguageTool:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)

    def check(self, text: str):
        return self.payloads.pop(0), 0.001


class FakeGemma:
    def __init__(self, output: str):
        self.output = output
        self.calls = 0

    def generate(self, model: str, text: str, instruction: str):
        self.calls += 1
        return self.output, {"eval_count": 2, "eval_duration": 1_000_000_000}


def test_rejected_output_falls_back_to_safe_result():
    source = "She finish today."
    match_item = raw_match(4, 6, ["finishes", "finished"], "HE_VERB_AGR")
    lt = FakeLanguageTool(
        [{"matches": [match_item]}, {"matches": [match_item]}]
    )
    gemma = FakeGemma("Corrected text: She finishes today.")
    record = hybrid.hybrid_case(
        {
            "id": "grammar-test",
            "category": "grammar",
            "input": source,
            "expected": "She finishes today.",
        },
        lt,
        gemma,
        "gemma3:4b",
    )
    assert record["routing_decision"] is True
    assert record["gemma_output_accepted"] is False
    assert record["final_output"] == source


def test_already_correct_case_never_invokes_gemma():
    source = "The report is ready."
    lt = FakeLanguageTool([{"matches": []}, {"matches": []}])
    gemma = FakeGemma("This should never be used.")
    record = hybrid.hybrid_case(
        {
            "id": "correct-test",
            "category": "already_correct",
            "input": source,
            "expected": source,
        },
        lt,
        gemma,
        "gemma3:4b",
    )
    assert record["routing_decision"] is False
    assert gemma.calls == 0
    assert record["final_output"] == source
