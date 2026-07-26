#!/usr/bin/env python3
"""Raw LanguageTool 6.6 baseline for the proofreading benchmark dataset.

This harness is intentionally independent of the production application. It
uses only the repository-bundled Java and LanguageTool server, requests en-US
explicitly, and records suggestions without adopting an automatic correction
policy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


LANGUAGE = "en-US"
SERVER_MAIN_CLASS = "org.languagetool.server.HTTPServer"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JAVA = REPOSITORY_ROOT / "vendor" / "java" / "bin" / (
    "java.exe" if os.name == "nt" else "java"
)
DEFAULT_SERVER_JAR = (
    REPOSITORY_ROOT / "vendor" / "languagetool" / "languagetool-server.jar"
)

SAFE = "SAFE"
AMBIGUOUS = "AMBIGUOUS"
IGNORE = "IGNORE"

# Initial Phase 18C policy derived from the 105-case Phase 18B evidence. Rules
# with only one or two positive examples remain AMBIGUOUS even when those
# examples were corrected exactly. The spelling rule is SAFE only under the
# additional replacement-selection constraints in select_safe_replacement().
RULE_POLICY = {
    "MORFOLOGIK_RULE_EN_US": SAFE,
    "AGREEMENT_SENT_START": AMBIGUOUS,
    "AUXILIARY_DO_WITH_INCORRECT_VERB_FORM": AMBIGUOUS,
    "CALENDER": AMBIGUOUS,
    "CD_NN": AMBIGUOUS,
    "CONDITIONAL_CLAUSE": AMBIGUOUS,
    "CONFUSION_OF_ME_I": AMBIGUOUS,
    "EXPLAIN_TO": AMBIGUOUS,
    "HE_VERB_AGR": AMBIGUOUS,
    "I_AM_VB": AMBIGUOUS,
    "IN_WEEKDAY": AMBIGUOUS,
    "MENTION_ABOUT": AMBIGUOUS,
    "MOST_COMPARATIVE": AMBIGUOUS,
    "NON3PRS_VERB": AMBIGUOUS,
    "PERS_PRONOUN_AGREEMENT": AMBIGUOUS,
    "PLEASE_VB": AMBIGUOUS,
    "SHE_LIVE": AMBIGUOUS,
    "SINCE_FOR": AMBIGUOUS,
    "THERE_S_MANY": AMBIGUOUS,
    "BEEN_PART_AGREEMENT": IGNORE,
    "EN_A_VS_AN": IGNORE,
    "RETURN_BACK": IGNORE,
    "THIS_NNS": IGNORE,
}

RULE_POLICY_RATIONALES = {
    "MORFOLOGIK_RULE_EN_US": (
        "Broad evidence (31 matches/30 cases), no unchanged-case triggers, and "
        "deterministic token-local spelling replacements under strict selection."
    ),
    "AGREEMENT_SENT_START": (
        "One case produced competing agreement edits; only one was needed."
    ),
    "AUXILIARY_DO_WITH_INCORRECT_VERB_FORM": (
        "One successful case is insufficient evidence for broad automation."
    ),
    "CALENDER": (
        "One context-dependent confused-word case; 'calender' is also a valid noun."
    ),
    "CD_NN": "One successful number-agreement example is insufficient evidence.",
    "CONDITIONAL_CLAUSE": (
        "One successful but context-dependent tense rewrite is insufficient evidence."
    ),
    "CONFUSION_OF_ME_I": (
        "One prescriptive pronoun/order rewrite may alter informal tone."
    ),
    "EXPLAIN_TO": (
        "Returned a grammatical alternative that did not construct the benchmark target."
    ),
    "HE_VERB_AGR": (
        "Both cases returned multiple tense choices requiring context."
    ),
    "I_AM_VB": "Returned multiple tense/aspect choices requiring context.",
    "IN_WEEKDAY": "One successful preposition example is insufficient evidence.",
    "MENTION_ABOUT": (
        "One successful collocation rewrite is insufficient evidence."
    ),
    "MOST_COMPARATIVE": (
        "One successful comparative rewrite is insufficient evidence."
    ),
    "NON3PRS_VERB": (
        "One successful agreement example is insufficient evidence."
    ),
    "PERS_PRONOUN_AGREEMENT": (
        "Returned multiple agreement/tense choices requiring context."
    ),
    "PLEASE_VB": "One successful imperative example is insufficient evidence.",
    "SHE_LIVE": "Returned multiple tense choices requiring context.",
    "SINCE_FOR": "One successful duration-preposition example is insufficient evidence.",
    "THERE_S_MANY": (
        "One successful agreement example is insufficient evidence."
    ),
    "BEEN_PART_AGREEMENT": (
        "Suggested 'engineered'/'engineering' for 'engineer'; desired article insertion "
        "was absent and the suggestions changed meaning."
    ),
    "EN_A_VS_AN": (
        "Suggested 'an information', which remains incorrect for the benchmark sentence."
    ),
    "RETURN_BACK": (
        "Optional redundancy deletion used an empty token replacement that left doubled "
        "whitespace with literal offset application."
    ),
    "THIS_NNS": (
        "Suggested 'These criteria', conflicting with the benchmark's equally valid "
        "'This criterion' correction path."
    ),
}

# LanguageTool ranks several common misspellings alongside unrelated valid
# words. These choices are explicit rather than a general "take first" rule.
SAFE_LEXICAL_REPLACEMENTS = {
    "adress": "address",
    "imediately": "immediately",
    "recieved": "received",
}

MORFOLOGIK_FAILURE_ANALYSIS = {
    "mixed-001": (
        "The desired 'employees' is present but is the third of six replacements; "
        "the first suggestion 'employs' is wrong here, and LanguageTool also missed "
        "the required 'is' to 'are' agreement edit."
    ),
    "mixed-002": (
        "The desired context-inflected 'received' is absent; only 'receive' and "
        "'relieve' are offered. Past-tense context is required."
    ),
    "mixed-004": (
        "The sole spelling replacement 'cancellations' is correct, but LanguageTool "
        "missed the separate 'was' to 'were' agreement edit."
    ),
    "mixed-006": (
        "The desired 'received' is absent; only 'receive' and 'relieve' are offered, "
        "and LanguageTool also missed 'have' to 'has'. Context is required."
    ),
    "mixed-008": (
        "The sole spelling replacement 'equipment' is correct, but LanguageTool "
        "missed the separate 'These' to 'This' agreement edit."
    ),
    "mixed-009": (
        "The desired 'analysis' is the first of three replacements, but selecting "
        "singular versus plural requires context; LanguageTool also missed 'were' "
        "to 'was'."
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark raw LanguageTool suggestions without integrating or "
            "automatically applying them in production."
        )
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
        default=Path(__file__).with_name("results") / "languagetool",
        help="Generated output directory (gitignored by default).",
    )
    parser.add_argument(
        "--java",
        type=Path,
        default=DEFAULT_JAVA,
        help="Bundled Java executable path.",
    )
    parser.add_argument(
        "--server-jar",
        type=Path,
        default=DEFAULT_SERVER_JAR,
        help="Bundled LanguageTool server JAR path.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback server host (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Private LanguageTool server port (default: %(default)s).",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for the bundled server to become ready.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30.0,
        help="Per-case HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N cases (smoke testing only).",
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) < 100:
        raise ValueError("Dataset must be a JSON array containing at least 100 cases.")
    required = {"id", "category", "input", "expected"}
    allowed_categories = {
        "already_correct",
        "grammar",
        "spelling",
        "mixed",
        "formatting",
    }
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each dataset case must be an object.")
        missing = required - set(case)
        if missing:
            raise ValueError(f"Case is missing fields {sorted(missing)}: {case!r}")
        if case["id"] in ids:
            raise ValueError(f"Duplicate case id: {case['id']}")
        ids.add(case["id"])
        if case["category"] not in allowed_categories:
            raise ValueError(f"Unknown category in {case['id']}")
        if not all(isinstance(case[key], str) for key in required):
            raise ValueError(f"String field has invalid type in {case['id']}")
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


def safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def formatting_signature(value: str) -> dict[str, Any]:
    lines = value.splitlines()
    return {
        "newline_count": value.count("\n"),
        "trailing_newline": value.endswith("\n"),
        "blank_line_indexes": [index for index, line in enumerate(lines) if not line],
        "line_prefixes": [
            line[: len(line) - len(line.lstrip(" \t-*•0123456789."))]
            for line in lines
        ],
    }


def validate_bundled_runtime(java: Path, server_jar: Path) -> tuple[Path, Path]:
    resolved_java = java.resolve()
    resolved_jar = server_jar.resolve()
    for path, label in ((resolved_java, "Java executable"), (resolved_jar, "server JAR")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    return resolved_java, resolved_jar


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


@dataclass
class LanguageToolServer:
    java: Path
    server_jar: Path
    host: str
    port: int
    startup_timeout: float
    process: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> "LanguageToolServer":
        java, jar = validate_bundled_runtime(self.java, self.server_jar)
        command = [
            str(java),
            "-cp",
            str(jar),
            SERVER_MAIN_CLASS,
            "--port",
            str(self.port),
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(jar.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=hidden_startupinfo(),
        )
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Bundled LanguageTool server exited with code "
                    f"{self.process.returncode} before becoming ready."
                )
            try:
                request = urllib.request.Request(
                    f"{self.base_url}/v2/languages", method="GET"
                )
                with urllib.request.urlopen(request, timeout=1.0) as response:
                    if response.status == 200:
                        return self
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(0.1)
        self.stop()
        raise TimeoutError(
            f"Bundled LanguageTool server did not become ready at {self.base_url}: "
            f"{last_error}"
        )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


@dataclass(frozen=True)
class LanguageToolClient:
    base_url: str
    timeout: float

    def check(self, text: str) -> tuple[dict[str, Any], float]:
        body = urllib.parse.urlencode(
            {"language": LANGUAGE, "text": text}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v2/check",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latency = time.perf_counter() - started
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise ValueError("LanguageTool returned an invalid response.")
        return payload, latency


def normalize_matches(payload: dict[str, Any], text: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get("matches", [])):
        if not isinstance(raw, dict):
            continue
        offset = raw.get("offset")
        length = raw.get("length")
        if not isinstance(offset, int) or not isinstance(length, int):
            continue
        if offset < 0 or length < 0 or offset + length > len(text):
            continue
        rule = raw.get("rule") if isinstance(raw.get("rule"), dict) else {}
        category = (
            rule.get("category")
            if isinstance(rule.get("category"), dict)
            else {}
        )
        replacements = []
        for replacement in raw.get("replacements", []):
            value = replacement.get("value") if isinstance(replacement, dict) else None
            if isinstance(value, str) and value not in replacements:
                replacements.append(value)
        original = text[offset : offset + length]
        normalized.append(
            {
                "match_index": index,
                "offset": offset,
                "length": length,
                "original_text": original,
                "message": str(raw.get("message", "")),
                "short_message": str(raw.get("shortMessage", "")),
                "replacements": replacements,
                "actionable_replacements": [
                    value for value in replacements if value != original
                ],
                "rule_id": str(rule.get("id", "")),
                "rule_description": str(rule.get("description", "")),
                "rule_issue_type": str(rule.get("issueType", "")),
                "category_id": str(category.get("id", "")),
                "category_name": str(category.get("name", "")),
                "context": raw.get("context"),
                "sentence": raw.get("sentence"),
                "type": raw.get("type"),
            }
        )
    return sorted(
        normalized,
        key=lambda item: (item["offset"], -item["length"], item["match_index"]),
    )


def find_expected_edit_path(
    source: str, expected: str, matches: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Return one non-overlapping LanguageTool edit path to expected, if any.

    Skipping every suggestion is always permitted. This is suggestion
    reachability evidence, not an automatic correction policy.
    """

    @lru_cache(maxsize=None)
    def search(
        match_index: int, source_cursor: int, expected_cursor: int
    ) -> tuple[tuple[int, str], ...] | None:
        if match_index >= len(matches):
            return (
                ()
                if source[source_cursor:] == expected[expected_cursor:]
                else None
            )
        match_item = matches[match_index]
        skipped = search(match_index + 1, source_cursor, expected_cursor)
        if skipped is not None:
            return skipped
        start = match_item["offset"]
        end = start + match_item["length"]
        if start < source_cursor:
            return None
        unchanged = source[source_cursor:start]
        if not expected.startswith(unchanged, expected_cursor):
            return None
        replacement_cursor = expected_cursor + len(unchanged)
        for replacement in match_item["actionable_replacements"]:
            if not expected.startswith(replacement, replacement_cursor):
                continue
            tail = search(
                match_index + 1,
                end,
                replacement_cursor + len(replacement),
            )
            if tail is not None:
                return ((match_index, replacement),) + tail
        return None

    path = search(0, 0, 0)
    if path is None:
        return None
    return [
        {
            "match_index": matches[index]["match_index"],
            "offset": matches[index]["offset"],
            "length": matches[index]["length"],
            "original_text": matches[index]["original_text"],
            "replacement": replacement,
            "rule_id": matches[index]["rule_id"],
            "category_id": matches[index]["category_id"],
        }
        for index, replacement in path
    ]


def apply_edits(source: str, edits: list[dict[str, Any]]) -> str:
    output = source
    for edit in sorted(edits, key=lambda item: item["offset"], reverse=True):
        start = edit["offset"]
        output = output[:start] + edit["replacement"] + output[start + edit["length"] :]
    return output


def validate_rule_policy(observed_rule_ids: set[str] | None = None) -> None:
    if set(RULE_POLICY.values()) != {SAFE, AMBIGUOUS, IGNORE}:
        raise ValueError("Rule policy must contain all three policy groups.")
    missing_rationales = set(RULE_POLICY) - set(RULE_POLICY_RATIONALES)
    if missing_rationales:
        raise ValueError(
            f"Rule policy is missing rationales for {sorted(missing_rationales)}"
        )
    if observed_rule_ids is not None:
        unclassified = observed_rule_ids - set(RULE_POLICY)
        if unclassified:
            raise ValueError(
                "Rule policy must classify every observed rule ID; "
                f"unclassified={sorted(unclassified)}"
            )


def _case_pattern(value: str) -> str:
    if value.islower():
        return "lower"
    if value.isupper():
        return "upper"
    if value.istitle():
        return "title"
    return "mixed"


def select_safe_replacement(
    match_item: dict[str, Any],
) -> tuple[str | None, str]:
    rule_id = match_item["rule_id"]
    policy_group = RULE_POLICY.get(rule_id)
    if policy_group is None:
        return None, "unclassified_rule"
    if policy_group == AMBIGUOUS:
        return None, "ambiguous_rule"
    if policy_group == IGNORE:
        return None, "ignored_rule"

    replacements = match_item["actionable_replacements"]
    if not replacements:
        return None, "no_actionable_replacement"
    original = match_item["original_text"]
    if not original.isalpha():
        return None, "non_word_source"

    selected: str | None = None
    acceptance_reason = ""
    if len(replacements) == 1:
        selected = replacements[0]
        acceptance_reason = "single_token_spelling_candidate"
    else:
        approved = SAFE_LEXICAL_REPLACEMENTS.get(original.casefold())
        if approved is not None:
            selected = next(
                (
                    candidate
                    for candidate in replacements
                    if candidate.casefold() == approved
                ),
                None,
            )
        if selected is None:
            return None, "multiple_replacements_without_approved_choice"
        acceptance_reason = "approved_lexical_choice"

    if not selected.isalpha():
        return None, "replacement_not_single_word"
    if _case_pattern(original) != _case_pattern(selected):
        return None, "replacement_changes_case_pattern"
    return selected, acceptance_reason


def edits_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = left["offset"]
    right_start = right["offset"]
    left_end = left_start + left["length"]
    right_end = right_start + right["length"]
    if left["length"] == 0 and right["length"] == 0:
        return left_start == right_start
    if left["length"] == 0:
        return right_start <= left_start <= right_end
    if right["length"] == 0:
        return left_start <= right_start <= left_end
    return max(left_start, right_start) < min(left_end, right_end)


def safe_filter(
    source: str, matches: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]], float]:
    """Apply only deterministic SAFE edits and retain an audit decision per match."""

    started = time.perf_counter()
    decisions: list[dict[str, Any]] = []
    provisional: dict[int, dict[str, Any]] = {}
    for match_item in matches:
        replacement, reason = select_safe_replacement(match_item)
        decision = {
            "match_index": match_item["match_index"],
            "rule_id": match_item["rule_id"],
            "category_id": match_item["category_id"],
            "policy_group": RULE_POLICY.get(match_item["rule_id"], "UNCLASSIFIED"),
            "offset": match_item["offset"],
            "length": match_item["length"],
            "original_text": match_item["original_text"],
            "replacement_candidates": match_item["actionable_replacements"],
            "accepted": replacement is not None,
            "selected_replacement": replacement,
            "reason": reason,
        }
        decisions.append(decision)
        if replacement is not None:
            provisional[match_item["match_index"]] = match_item

    actionable = [
        match_item
        for match_item in matches
        if match_item["actionable_replacements"]
    ]
    for decision in decisions:
        if not decision["accepted"]:
            continue
        candidate = provisional[decision["match_index"]]
        conflict = any(
            other["match_index"] != candidate["match_index"]
            and edits_overlap(candidate, other)
            for other in actionable
        )
        if conflict:
            decision["accepted"] = False
            decision["selected_replacement"] = None
            decision["reason"] = "overlapping_or_conflicting_match"

    edits = [
        {
            "offset": decision["offset"],
            "length": decision["length"],
            "replacement": decision["selected_replacement"],
        }
        for decision in decisions
        if decision["accepted"]
    ]
    output = apply_edits(source, edits)
    return output, decisions, time.perf_counter() - started


def case_record(
    case: dict[str, Any],
    payload: dict[str, Any] | None,
    latency: float,
    error: str | None = None,
) -> dict[str, Any]:
    expected_change = case["input"] != case["expected"]
    matches = normalize_matches(payload or {"matches": []}, case["input"])
    actionable = [
        match_item
        for match_item in matches
        if match_item["actionable_replacements"]
    ]
    edit_path = (
        find_expected_edit_path(case["input"], case["expected"], matches)
        if error is None
        else None
    )
    reachable = edit_path is not None if error is None else False
    evidence_output = (
        apply_edits(case["input"], edit_path)
        if edit_path is not None
        else case["input"]
    )
    exact_preservation = (
        not actionable if not expected_change and error is None else None
    )
    formatting_result = (
        formatting_signature(evidence_output)
        == formatting_signature(case["expected"])
        if case["category"] == "formatting" and error is None
        else None
    )
    safe_output, safe_decisions, filter_latency = safe_filter(case["input"], matches)
    accepted_decisions = [
        decision for decision in safe_decisions if decision["accepted"]
    ]
    rejection_reasons = Counter(
        decision["reason"]
        for decision in safe_decisions
        if not decision["accepted"]
    )
    safe_formatting_result = (
        formatting_signature(safe_output)
        == formatting_signature(case["expected"])
        if case["category"] == "formatting" and error is None
        else None
    )
    language_info = (payload or {}).get("language", {})
    return {
        "engine": "LanguageTool 6.6",
        "language_requested": LANGUAGE,
        "language_response": language_info,
        "case_id": case["id"],
        "category": case["category"],
        "input": case["input"],
        "expected_output": case["expected"],
        "expected_change": expected_change,
        "latency_seconds": latency,
        "raw_match_count": len(matches),
        "actionable_match_count": len(actionable),
        "triggered_rule_ids": sorted(
            {match_item["rule_id"] for match_item in matches if match_item["rule_id"]}
        ),
        "triggered_categories": sorted(
            {
                (match_item["category_id"], match_item["category_name"])
                for match_item in matches
                if match_item["category_id"] or match_item["category_name"]
            }
        ),
        "raw_response": payload,
        "matches": matches,
        "expected_output_reachable": reachable,
        "supporting_edits": edit_path or [],
        "evidence_output": evidence_output,
        "exact_match": evidence_output == case["expected"] if error is None else False,
        "exact_preservation": exact_preservation,
        "unnecessary_edit_signal": (
            not expected_change and bool(actionable) if error is None else False
        ),
        "missed_correction": (
            expected_change and not reachable if error is None else False
        ),
        "formatting_preservation": formatting_result,
        "safe_output": safe_output,
        "safe_output_changed": safe_output != case["input"] if error is None else False,
        "safe_exact_match": safe_output == case["expected"] if error is None else False,
        "safe_exact_preservation": (
            safe_output == case["input"]
            if not expected_change and error is None
            else None
        ),
        "safe_unnecessary_edit": (
            safe_output != case["input"]
            if not expected_change and error is None
            else False
        ),
        "safe_missed_correction": (
            expected_change and safe_output != case["expected"]
            if error is None
            else False
        ),
        "safe_formatting_preservation": safe_formatting_result,
        "safe_corrections_applied": len(accepted_decisions),
        "safe_matches_rejected": len(safe_decisions) - len(accepted_decisions),
        "safe_rejection_reasons": dict(rejection_reasons),
        "safe_decisions": safe_decisions,
        "safe_filter_latency_seconds": filter_latency,
        "safe_total_latency_seconds": latency + filter_latency,
        "error": error,
    }


def analyze_rule_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    observed_rule_ids = sorted(
        {
            match_item["rule_id"]
            for record in records
            for match_item in record["matches"]
            if match_item["rule_id"]
        }
    )
    validate_rule_policy(set(observed_rule_ids))
    for rule_id in observed_rule_ids:
        case_rows: list[dict[str, Any]] = []
        match_count = 0
        for record in records:
            rule_matches = [
                match_item
                for match_item in record["matches"]
                if match_item["rule_id"] == rule_id
            ]
            if not rule_matches:
                continue
            match_count += len(rule_matches)
            case_rows.append(
                {
                    "case_id": record["case_id"],
                    "dataset_category": record["category"],
                    "expected_change": record["expected_change"],
                    "expected_output_reachable": record[
                        "expected_output_reachable"
                    ],
                    "input": record["input"],
                    "expected_output": record["expected_output"],
                    "matches": [
                        {
                            "original_text": match_item["original_text"],
                            "replacements": match_item["replacements"],
                            "offset": match_item["offset"],
                            "length": match_item["length"],
                        }
                        for match_item in rule_matches
                    ],
                }
            )
        correction_rows = [row for row in case_rows if row["expected_change"]]
        unchanged_rows = [row for row in case_rows if not row["expected_change"]]
        reachable_rows = [
            row for row in correction_rows if row["expected_output_reachable"]
        ]
        failure_rows = [
            row for row in correction_rows if not row["expected_output_reachable"]
        ]
        ambiguous_rows = [
            row
            for row in case_rows
            if RULE_POLICY[rule_id] != SAFE
            or not row["expected_output_reachable"]
            or any(len(match_item["replacements"]) != 1 for match_item in row["matches"])
        ]
        evidence.append(
            {
                "rule_id": rule_id,
                "category_ids": sorted(
                    {
                        match_item["category_id"]
                        for record in records
                        for match_item in record["matches"]
                        if match_item["rule_id"] == rule_id
                        and match_item["category_id"]
                    }
                ),
                "policy_group": RULE_POLICY[rule_id],
                "policy_rationale": RULE_POLICY_RATIONALES[rule_id],
                "sufficient_evidence_to_automate": RULE_POLICY[rule_id] == SAFE,
                "total_match_count": match_count,
                "affected_case_count": len(case_rows),
                "expected_correction_case_count": len(correction_rows),
                "expected_unchanged_case_count": len(unchanged_rows),
                "expected_result_constructible_case_count": len(reachable_rows),
                "expected_result_constructible_rate": safe_rate(
                    len(reachable_rows), len(correction_rows)
                ),
                "failure_count": len(failure_rows),
                "failure_case_ids": [row["case_id"] for row in failure_rows],
                "ambiguous_examples": ambiguous_rows,
            }
        )
    return evidence


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [record for record in records if not record["error"]]
    correct = [
        record for record in usable if record["category"] == "already_correct"
    ]
    unchanged = [record for record in usable if not record["expected_change"]]
    correction = [record for record in usable if record["expected_change"]]
    formatting = [
        record for record in usable if record["category"] == "formatting"
    ]
    latencies = [record["latency_seconds"] for record in usable]
    rule_matches: Counter[str] = Counter()
    rule_cases: Counter[str] = Counter()
    category_matches: Counter[str] = Counter()
    category_cases: Counter[str] = Counter()
    supporting_rule_edits: Counter[str] = Counter()
    supporting_category_edits: Counter[str] = Counter()
    unnecessary_rule_cases: Counter[str] = Counter()
    unnecessary_category_cases: Counter[str] = Counter()
    for record in usable:
        case_rules: set[str] = set()
        case_categories: set[str] = set()
        for match_item in record["matches"]:
            rule_id = match_item["rule_id"] or "(unknown)"
            category_id = match_item["category_id"] or "(unknown)"
            rule_matches[rule_id] += 1
            category_matches[category_id] += 1
            case_rules.add(rule_id)
            case_categories.add(category_id)
        rule_cases.update(case_rules)
        category_cases.update(case_categories)
        for edit in record["supporting_edits"]:
            supporting_rule_edits[edit["rule_id"] or "(unknown)"] += 1
            supporting_category_edits[edit["category_id"] or "(unknown)"] += 1
        if record["unnecessary_edit_signal"]:
            unnecessary_rule_cases.update(case_rules)
            unnecessary_category_cases.update(case_categories)
    safe_rejection_reasons: Counter[str] = Counter()
    for record in usable:
        safe_rejection_reasons.update(record["safe_rejection_reasons"])
    safe_filter_latencies = [
        record["safe_filter_latency_seconds"] for record in usable
    ]
    safe_total_latencies = [
        record["safe_total_latency_seconds"] for record in usable
    ]
    total = len(records)
    return {
        "quality_case_count": total,
        "successful_case_count": len(usable),
        "exact_preservation_rate": safe_rate(
            sum(record["exact_preservation"] is True for record in correct),
            len(correct),
        ),
        "exact_correction_accuracy": safe_rate(
            sum(record["expected_output_reachable"] for record in correction),
            len(correction),
        ),
        "over_edit_rate": safe_rate(
            sum(record["unnecessary_edit_signal"] for record in unchanged),
            len(unchanged),
        ),
        "formatting_preservation_rate": safe_rate(
            sum(record["formatting_preservation"] is True for record in formatting),
            len(formatting),
        ),
        "mean_latency_seconds": statistics.fmean(latencies) if latencies else None,
        "median_latency_seconds": statistics.median(latencies) if latencies else None,
        "p95_latency_seconds": percentile(latencies, 0.95),
        "raw_match_count": sum(record["raw_match_count"] for record in usable),
        "actionable_match_count": sum(
            record["actionable_match_count"] for record in usable
        ),
        "unnecessary_edit_signal_count": sum(
            record["unnecessary_edit_signal"] for record in usable
        ),
        "missed_correction_count": sum(
            record["missed_correction"] for record in usable
        ),
        "formatting_failure_count": sum(
            record["formatting_preservation"] is False for record in formatting
        ),
        "error_count": total - len(usable),
        "error_rate": safe_rate(total - len(usable), total),
        "triggered_rules": [
            {
                "rule_id": rule_id,
                "match_count": count,
                "case_count": rule_cases[rule_id],
                "supporting_edit_count": supporting_rule_edits[rule_id],
                "unnecessary_signal_case_count": unnecessary_rule_cases[rule_id],
            }
            for rule_id, count in rule_matches.most_common()
        ],
        "triggered_categories": [
            {
                "category_id": category_id,
                "match_count": count,
                "case_count": category_cases[category_id],
                "supporting_edit_count": supporting_category_edits[category_id],
                "unnecessary_signal_case_count": unnecessary_category_cases[category_id],
            }
            for category_id, count in category_matches.most_common()
        ],
        "safe_filter": {
            "exact_preservation_rate": safe_rate(
                sum(
                    record["safe_exact_preservation"] is True
                    for record in correct
                ),
                len(correct),
            ),
            "exact_correction_accuracy": safe_rate(
                sum(record["safe_exact_match"] for record in correction),
                len(correction),
            ),
            "over_edit_rate": safe_rate(
                sum(record["safe_unnecessary_edit"] for record in unchanged),
                len(unchanged),
            ),
            "formatting_preservation_rate": safe_rate(
                sum(
                    record["safe_formatting_preservation"] is True
                    for record in formatting
                ),
                len(formatting),
            ),
            "cases_changed": sum(record["safe_output_changed"] for record in usable),
            "corrections_applied": sum(
                record["safe_corrections_applied"] for record in usable
            ),
            "matches_rejected": sum(
                record["safe_matches_rejected"] for record in usable
            ),
            "rejection_reasons": dict(safe_rejection_reasons.most_common()),
            "missed_correction_count": sum(
                record["safe_missed_correction"] for record in correction
            ),
            "mean_filter_latency_seconds": (
                statistics.fmean(safe_filter_latencies)
                if safe_filter_latencies
                else None
            ),
            "median_filter_latency_seconds": (
                statistics.median(safe_filter_latencies)
                if safe_filter_latencies
                else None
            ),
            "p95_filter_latency_seconds": percentile(safe_filter_latencies, 0.95),
            "mean_total_latency_seconds": (
                statistics.fmean(safe_total_latencies)
                if safe_total_latencies
                else None
            ),
            "median_total_latency_seconds": (
                statistics.median(safe_total_latencies)
                if safe_total_latencies
                else None
            ),
            "p95_total_latency_seconds": percentile(safe_total_latencies, 0.95),
        },
    }


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def markdown_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    filtered = summary["safe_filter"]
    lines = [
        "# LanguageTool 6.6 Raw and SAFE-Filter Benchmark",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "This remains benchmark-only. No LanguageTool suggestion was sent to the "
        "application or applied to user text, and production behavior was not changed.",
        "",
        "## Configuration",
        "",
        f"- Dataset cases: {result['dataset']['size']}",
        f"- Requested language: `{result['language']}` (explicit on every request)",
        f"- Java: `{result['runtime']['java']}`",
        f"- Server JAR: `{result['runtime']['server_jar']}`",
        "",
        "## Raw reachability versus deterministic SAFE output",
        "",
        "| Mode | Exact preservation | Exact correction | Over-edit | Formatting | Mean s | Median s | P95 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Raw suggestion reachability | "
        f"{pct(summary['exact_preservation_rate'])} | "
        f"{pct(summary['exact_correction_accuracy'])} | "
        f"{pct(summary['over_edit_rate'])} | "
        f"{pct(summary['formatting_preservation_rate'])} | "
        f"{num(summary['mean_latency_seconds'])} | "
        f"{num(summary['median_latency_seconds'])} | "
        f"{num(summary['p95_latency_seconds'])} |",
        f"| Actual SAFE-filter output | "
        f"{pct(filtered['exact_preservation_rate'])} | "
        f"{pct(filtered['exact_correction_accuracy'])} | "
        f"{pct(filtered['over_edit_rate'])} | "
        f"{pct(filtered['formatting_preservation_rate'])} | "
        f"{num(filtered['mean_total_latency_seconds'])} | "
        f"{num(filtered['median_total_latency_seconds'])} | "
        f"{num(filtered['p95_total_latency_seconds'])} |",
        "",
        "Exact correction is a reachability ceiling: the expected output must be "
        "constructible from a non-overlapping subset of returned replacement "
        "candidates. SAFE-filter accuracy instead scores its actual deterministic "
        "output. Suggestions are not blindly replayed. Exact preservation "
        "requires zero actionable suggestions on an already-correct case; the "
        "over-edit signal uses every case whose expected output is unchanged.",
        "",
        f"SAFE mode changed {filtered['cases_changed']} cases, applied "
        f"{filtered['corrections_applied']} corrections, and rejected "
        f"{filtered['matches_rejected']} matches.",
        "",
        "## SAFE policy",
        "",
        "- Apply only explicitly SAFE rule IDs.",
        "- Require a token-local alphabetic replacement with the same case pattern.",
        "- Accept one candidate, or one explicit evidence-backed lexical choice.",
        "- Reject unclassified, AMBIGUOUS, IGNORE, missing, multi-candidate, "
        "overlapping, and conflicting matches.",
        "- Apply accepted independent edits from the end of the text backward so "
        "LanguageTool offsets remain valid.",
        "",
    ]
    for group in (SAFE, AMBIGUOUS, IGNORE):
        rules = result["policy"]["safe_filter"]["rule_policy"][group]
        lines.append(f"- {group} ({len(rules)}): " + ", ".join(f"`{rule}`" for rule in rules))
    lines.extend(
        [
            "",
            "Rejection reasons: "
            + ", ".join(
                f"`{reason}`={count}"
                for reason, count in filtered["rejection_reasons"].items()
            ),
            "",
            "## Rule-by-rule evidence and classification",
            "",
            "| Rule ID | Group | Matches | Cases | Correction | Unchanged | Constructible | Failures | Automate? |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in result["rule_evidence"]:
        lines.append(
            f"| `{item['rule_id']}` | {item['policy_group']} | "
            f"{item['total_match_count']} | {item['affected_case_count']} | "
            f"{item['expected_correction_case_count']} | "
            f"{item['expected_unchanged_case_count']} | "
            f"{item['expected_result_constructible_case_count']} "
            f"({pct(item['expected_result_constructible_rate'])}) | "
            f"{item['failure_count']} | "
            f"{item['sufficient_evidence_to_automate']} |"
        )
    lines.extend(
        [
            "",
            "Each rule's rationale, failure case IDs, replacement candidates, and "
            "ambiguous examples are retained in `latest.json`.",
            "",
            "## MORFOLOGIK_RULE_EN_US unreachable cases",
            "",
        ]
    )
    for item in result["morfologik_unreachable_cases"]:
        lines.append(f"- `{item['case_id']}`: {item['analysis']}")
    if not result["morfologik_unreachable_cases"]:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Triggered categories",
        "",
        "| Category ID | Matches | Cases | Exact-path edits | Over-edit cases |",
        "|---|---:|---:|---:|---:|",
        ]
    )
    for item in summary["triggered_categories"]:
        lines.append(
            f"| `{item['category_id']}` | {item['match_count']} | "
            f"{item['case_count']} | {item['supporting_edit_count']} | "
            f"{item['unnecessary_signal_case_count']} |"
        )
    if not summary["triggered_categories"]:
        lines.append("| none | 0 | 0 | 0 | 0 |")
    lines.extend(
        [
            "",
            "Full raw matches, SAFE accept/reject decisions, replacement candidates, "
            "supporting edit paths, latencies, and errors are retained in "
            "`latest.json`; flat case-level metrics are in `latest.csv`.",
            "",
            "Production model behavior, prompt, hotkey handling, sanitizer, "
            "chunking, and default model were not changed.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "engine",
        "language_requested",
        "case_id",
        "category",
        "input",
        "expected_output",
        "expected_change",
        "latency_seconds",
        "raw_match_count",
        "actionable_match_count",
        "triggered_rule_ids",
        "triggered_categories",
        "expected_output_reachable",
        "supporting_edits",
        "evidence_output",
        "exact_match",
        "exact_preservation",
        "unnecessary_edit_signal",
        "missed_correction",
        "formatting_preservation",
        "safe_output",
        "safe_output_changed",
        "safe_exact_match",
        "safe_exact_preservation",
        "safe_unnecessary_edit",
        "safe_missed_correction",
        "safe_formatting_preservation",
        "safe_corrections_applied",
        "safe_matches_rejected",
        "safe_rejection_reasons",
        "safe_filter_latency_seconds",
        "safe_total_latency_seconds",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            flat = {field: record.get(field) for field in fields}
            for field in (
                "triggered_rule_ids",
                "triggered_categories",
                "supporting_edits",
                "safe_rejection_reasons",
            ):
                flat[field] = json.dumps(flat[field], ensure_ascii=False)
            writer.writerow(flat)


def run_cases(
    client: LanguageToolClient, cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        try:
            payload, latency = client.check(case["input"])
            record = case_record(case, payload, latency)
        except (
            OSError,
            TimeoutError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ) as exc:
            record = case_record(
                case,
                None,
                0.0,
                f"{type(exc).__name__}: {exc}",
            )
        records.append(record)
        print(
            f"{index}/{len(cases)} {case['id']} "
            f"{record['latency_seconds']:.4f}s "
            f"matches={record['raw_match_count']}",
            flush=True,
        )
    return records


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    if not (1 <= args.port <= 65535):
        raise ValueError("--port must be between 1 and 65535")
    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]
    java, jar = validate_bundled_runtime(args.java, args.server_jar)
    with LanguageToolServer(
        java=java,
        server_jar=jar,
        host=args.host,
        port=args.port,
        startup_timeout=args.startup_timeout,
    ) as server:
        client = LanguageToolClient(server.base_url, args.request_timeout)
        records = run_cases(client, cases)
    rule_evidence = analyze_rule_evidence(records)
    morfologik_failures = [
        {
            "case_id": record["case_id"],
            "input": record["input"],
            "expected_output": record["expected_output"],
            "matches": [
                {
                    "original_text": match_item["original_text"],
                    "replacements": match_item["replacements"],
                    "offset": match_item["offset"],
                    "length": match_item["length"],
                }
                for match_item in record["matches"]
                if match_item["rule_id"] == "MORFOLOGIK_RULE_EN_US"
            ],
            "analysis": MORFOLOGIK_FAILURE_ANALYSIS[record["case_id"]],
        }
        for record in records
        if not record["expected_output_reachable"]
        and any(
            match_item["rule_id"] == "MORFOLOGIK_RULE_EN_US"
            for match_item in record["matches"]
        )
    ]
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "LanguageTool 6.6",
        "language": LANGUAGE,
        "policy": {
            "automatic_application": False,
            "exact_correction_definition": (
                "Expected output is reachable from a non-overlapping subset of "
                "LanguageTool replacement candidates."
            ),
            "exact_preservation_definition": (
                "No actionable LanguageTool replacement is returned for an "
                "already-correct case."
            ),
            "over_edit_definition": (
                "At least one actionable LanguageTool replacement is returned "
                "for any expected-unchanged case."
            ),
            "safe_filter": {
                "scope": "benchmark-only",
                "rule_policy": {
                    group: sorted(
                        rule_id
                        for rule_id, classification in RULE_POLICY.items()
                        if classification == group
                    )
                    for group in (SAFE, AMBIGUOUS, IGNORE)
                },
                "rule_rationales": RULE_POLICY_RATIONALES,
                "safe_lexical_replacements": SAFE_LEXICAL_REPLACEMENTS,
                "selection": (
                    "SAFE rules only; token-local alphabetic replacement; matching "
                    "case pattern; exactly one candidate or explicit lexical choice; "
                    "reject any overlap/conflict; apply accepted edits in reverse "
                    "offset order."
                ),
            },
        },
        "runtime": {
            "java": str(java),
            "server_jar": str(jar),
            "server_main_class": SERVER_MAIN_CLASS,
            "base_url": f"http://{args.host}:{args.port}",
            "software_response": next(
                (
                    record["raw_response"].get("software")
                    for record in records
                    if isinstance(record["raw_response"], dict)
                    and isinstance(record["raw_response"].get("software"), dict)
                ),
                None,
            ),
        },
        "dataset": {
            "path": str(args.cases),
            "size": len(cases),
            "categories": dict(Counter(case["category"] for case in cases)),
        },
        "summary": summarize(records),
        "rule_evidence": rule_evidence,
        "morfologik_unreachable_cases": morfologik_failures,
        "case_records": records,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.results_dir / "latest.json"
    csv_path = args.results_dir / "latest.csv"
    markdown_path = args.results_dir / "latest.md"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, records)
    markdown_path.write_text(markdown_report(result), encoding="utf-8")
    print(f"Wrote {json_path}, {csv_path}, and {markdown_path}")
    return 1 if result["summary"]["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
