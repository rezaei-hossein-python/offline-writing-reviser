from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "run_languagetool_benchmark.py"
SPEC = importlib.util.spec_from_file_location("languagetool_benchmark", SCRIPT)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def match(
    offset: int,
    length: int,
    replacements: list[str],
    rule_id: str = "TEST_RULE",
    category_id: str = "GRAMMAR",
) -> dict:
    return {
        "offset": offset,
        "length": length,
        "message": "Test match",
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
    return benchmark.normalize_matches({"matches": list(matches)}, source)


def test_bundled_runtime_defaults_are_repository_local():
    assert benchmark.DEFAULT_JAVA == ROOT / "vendor" / "java" / "bin" / "java.exe"
    assert benchmark.DEFAULT_SERVER_JAR == (
        ROOT / "vendor" / "languagetool" / "languagetool-server.jar"
    )


def test_client_posts_explicit_en_us(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"matches": [], "language": {}}).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(benchmark.urllib.request, "urlopen", fake_urlopen)
    client = benchmark.LanguageToolClient("http://127.0.0.1:8081", 7.0)
    payload, _ = client.check("Color is correct.")
    assert payload["matches"] == []
    assert captured["timeout"] == 7.0
    assert b"language=en-US" in captured["request"].data
    assert b"text=Color+is+correct." in captured["request"].data


def test_expected_reachability_uses_non_overlapping_subset():
    source = "The report are redy."
    expected = "The report is ready."
    raw = {
        "matches": [
            match(11, 3, ["is"], "SUBJECT_VERB"),
            match(15, 4, ["ready", "red"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
            match(11, 8, ["looks wrong"], "OVERLAPPING_STYLE", "STYLE"),
        ]
    }
    matches = benchmark.normalize_matches(raw, source)
    path = benchmark.find_expected_edit_path(source, expected, matches)
    assert path is not None
    assert [edit["replacement"] for edit in path] == ["is", "ready"]
    assert benchmark.apply_edits(source, path) == expected


def test_reachability_does_not_apply_unrelated_suggestion():
    source = "Please review the documnet today."
    expected = "Please review the document today."
    raw = {
        "matches": [
            match(18, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
            match(0, 6, ["Kindly"], "STYLE_RULE", "STYLE"),
        ]
    }
    matches = benchmark.normalize_matches(raw, source)
    path = benchmark.find_expected_edit_path(source, expected, matches)
    assert path is not None
    assert [edit["rule_id"] for edit in path] == ["MORFOLOGIK_RULE_EN_US"]


def test_case_record_treats_actionable_match_as_over_edit_signal():
    case = {
        "id": "correct-001",
        "category": "already_correct",
        "input": "The report is ready.",
        "expected": "The report is ready.",
    }
    payload = {"matches": [match(4, 6, ["document"], "STYLE_RULE", "STYLE")]}
    record = benchmark.case_record(case, payload, 0.01)
    assert record["expected_output_reachable"] is True
    assert record["exact_preservation"] is False
    assert record["unnecessary_edit_signal"] is True
    assert record["evidence_output"] == case["input"]


def test_rule_policy_classifies_every_observed_rule_exactly_once():
    benchmark.validate_rule_policy(set(benchmark.RULE_POLICY))
    counts = {
        group: list(benchmark.RULE_POLICY.values()).count(group)
        for group in (benchmark.SAFE, benchmark.AMBIGUOUS, benchmark.IGNORE)
    }
    assert counts == {
        benchmark.SAFE: 1,
        benchmark.AMBIGUOUS: 18,
        benchmark.IGNORE: 4,
    }


def test_safe_rule_with_one_token_candidate_is_accepted():
    source = "Review the documnet."
    matches = normalized(
        source,
        match(11, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == "Review the document."
    assert decisions[0]["accepted"] is True
    assert decisions[0]["reason"] == "single_token_spelling_candidate"


def test_ambiguous_rule_is_rejected():
    source = "She finish today."
    matches = normalized(
        source,
        match(4, 6, ["finishes", "finished"], "HE_VERB_AGR"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert decisions[0]["reason"] == "ambiguous_rule"


def test_ignore_rule_is_rejected():
    source = "We need a information."
    matches = normalized(
        source,
        match(8, 1, ["an"], "EN_A_VS_AN"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert decisions[0]["reason"] == "ignored_rule"


def test_safe_rule_without_replacement_is_rejected():
    source = "A documnet."
    matches = normalized(
        source,
        match(2, 8, [], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert decisions[0]["reason"] == "no_actionable_replacement"


def test_multiple_replacements_without_approved_choice_are_rejected():
    source = "The employes waited."
    matches = normalized(
        source,
        match(
            4,
            8,
            ["employs", "employed", "employees"],
            "MORFOLOGIK_RULE_EN_US",
            "TYPOS",
        ),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert decisions[0]["reason"] == "multiple_replacements_without_approved_choice"


def test_approved_lexical_choice_does_not_blindly_take_first_replacement():
    source = "We recieved it."
    matches = normalized(
        source,
        match(
            3,
            8,
            ["relieved", "received"],
            "MORFOLOGIK_RULE_EN_US",
            "TYPOS",
        ),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == "We received it."
    assert decisions[0]["selected_replacement"] == "received"
    assert decisions[0]["reason"] == "approved_lexical_choice"


def test_multiple_independent_edits_use_original_offsets_safely():
    source = "documnet and neccessary"
    matches = normalized(
        source,
        match(0, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
        match(13, 10, ["necessary"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == "document and necessary"
    assert sum(decision["accepted"] for decision in decisions) == 2


def test_overlapping_safe_edits_are_both_rejected():
    source = "documnet"
    matches = normalized(
        source,
        match(0, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
        match(0, 4, ["dock"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert {decision["reason"] for decision in decisions} == {
        "overlapping_or_conflicting_match"
    }


def test_conflicting_replacements_for_same_range_are_rejected():
    source = "documnet"
    matches = normalized(
        source,
        match(0, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
        match(0, 8, ["documents"], "MORFOLOGIK_RULE_EN_US", "TYPOS"),
    )
    output, decisions, _ = benchmark.safe_filter(source, matches)
    assert output == source
    assert all(not decision["accepted"] for decision in decisions)


def test_safe_edit_preserves_formatting_newlines_and_punctuation():
    source = "- Review the documnet.\n\n- Keep this line unchanged!\n"
    offset = source.index("documnet")
    matches = normalized(
        source,
        match(
            offset,
            8,
            ["document"],
            "MORFOLOGIK_RULE_EN_US",
            "TYPOS",
        ),
    )
    output, _, _ = benchmark.safe_filter(source, matches)
    assert output == "- Review the document.\n\n- Keep this line unchanged!\n"
    assert benchmark.formatting_signature(output) == benchmark.formatting_signature(
        source
    )


def test_unclassified_rule_and_no_matches_preserve_bytes_exactly():
    source = "Already correct.\r\nSecond line — unchanged!\r\n"
    unsafe = normalized(source, match(0, 7, ["Previously"], "UNKNOWN_RULE"))
    unsafe_output, decisions, _ = benchmark.safe_filter(source, unsafe)
    empty_output, empty_decisions, _ = benchmark.safe_filter(source, [])
    assert unsafe_output == source
    assert decisions[0]["reason"] == "unclassified_rule"
    assert empty_output == source
    assert empty_decisions == []


def test_summary_records_rule_and_category_counts():
    cases = [
        {
            "id": "correct",
            "category": "already_correct",
            "input": "This is correct.",
            "expected": "This is correct.",
        },
        {
            "id": "spelling",
            "category": "spelling",
            "input": "A documnet.",
            "expected": "A document.",
        },
    ]
    records = [
        benchmark.case_record(cases[0], {"matches": []}, 0.01),
        benchmark.case_record(
            cases[1],
            {
                "matches": [
                    match(2, 8, ["document"], "MORFOLOGIK_RULE_EN_US", "TYPOS")
                ]
            },
            0.02,
        ),
    ]
    summary = benchmark.summarize(records)
    assert summary["exact_preservation_rate"] == 1.0
    assert summary["exact_correction_accuracy"] == 1.0
    assert summary["over_edit_rate"] == 0.0
    assert summary["triggered_rules"][0] == {
        "rule_id": "MORFOLOGIK_RULE_EN_US",
        "match_count": 1,
        "case_count": 1,
        "supporting_edit_count": 1,
        "unnecessary_signal_case_count": 0,
    }
    assert summary["triggered_categories"][0]["category_id"] == "TYPOS"


def test_server_command_uses_bundled_paths_and_stops_owned_process(
    monkeypatch, tmp_path
):
    java = tmp_path / "java.exe"
    jar = tmp_path / "languagetool-server.jar"
    java.write_bytes(b"java")
    jar.write_bytes(b"jar")
    captured = {}

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = Process()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(benchmark.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        benchmark.urllib.request, "urlopen", lambda request, timeout: Response()
    )
    server = benchmark.LanguageToolServer(java, jar, "127.0.0.1", 8081, 1.0)
    with server:
        assert server.base_url == "http://127.0.0.1:8081"
    assert captured["command"] == [
        str(java.resolve()),
        "-cp",
        str(jar.resolve()),
        benchmark.SERVER_MAIN_CLASS,
        "--port",
        "8081",
    ]
    assert captured["kwargs"]["cwd"] == str(jar.resolve().parent)
    assert process.returncode == 0
