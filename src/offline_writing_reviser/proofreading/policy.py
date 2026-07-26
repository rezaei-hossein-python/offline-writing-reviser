from __future__ import annotations

import difflib
import math
import re
import time
from typing import Any


SAFE = "SAFE"
AMBIGUOUS = "AMBIGUOUS"
IGNORE = "IGNORE"
PROMPT_VERSION = "phase18d-v1"

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
        "Broad benchmark evidence with no unchanged-case triggers; automation "
        "still requires constrained token-local replacement selection."
    ),
    "AGREEMENT_SENT_START": "Competing agreement edits require context.",
    "AUXILIARY_DO_WITH_INCORRECT_VERB_FORM": "Sparse positive evidence.",
    "CALENDER": "Context-dependent confused word that can also be a valid noun.",
    "CD_NN": "Sparse number-agreement evidence.",
    "CONDITIONAL_CLAUSE": "Context-dependent tense rewrite.",
    "CONFUSION_OF_ME_I": "Prescriptive rewrite can alter informal tone.",
    "EXPLAIN_TO": "Observed alternative did not construct the target.",
    "HE_VERB_AGR": "Multiple tense choices require context.",
    "I_AM_VB": "Multiple tense/aspect choices require context.",
    "IN_WEEKDAY": "Sparse preposition evidence.",
    "MENTION_ABOUT": "Sparse collocation evidence.",
    "MOST_COMPARATIVE": "Sparse comparative evidence.",
    "NON3PRS_VERB": "Sparse agreement evidence.",
    "PERS_PRONOUN_AGREEMENT": "Agreement and tense require context.",
    "PLEASE_VB": "Sparse imperative evidence.",
    "SHE_LIVE": "Multiple tense choices require context.",
    "SINCE_FOR": "Sparse duration-preposition evidence.",
    "THERE_S_MANY": "Sparse agreement evidence.",
    "BEEN_PART_AGREEMENT": "Observed suggestions changed meaning.",
    "EN_A_VS_AN": "Observed suggestion remained ungrammatical.",
    "RETURN_BACK": "Observed empty replacement damaged whitespace.",
    "THIS_NNS": "Observed alternatives conflicted with an equally valid path.",
}

SAFE_LEXICAL_REPLACEMENTS = {
    "adress": "address",
    "imediately": "immediately",
    "recieved": "received",
}
CONTEXTUAL_SAFE_REJECTION_REASONS = {
    "multiple_replacements_without_approved_choice",
    "no_actionable_replacement",
    "replacement_not_single_word",
    "replacement_changes_case_pattern",
    "non_word_source",
}
AMBIGUOUS_NON_GRAMMAR_RULES = {"CALENDER"}

HYBRID_PROMPT = """You are the second stage of a conservative proofreading system.

Correct only genuine objective grammar or spelling errors in the supplied text.
Make the smallest possible changes.

Do not improve style, paraphrase, change tone, substitute optional vocabulary, or rewrite a sentence unless a short grammatical correction requires it.
Preserve meaning, facts, capitalization, punctuation, spacing, typography, paragraphs, line breaks, blank lines, list markers, and formatting.
The LanguageTool evidence below is advisory. Correct only errors that are genuinely supported by the text and context.
If no correction is needed, return the supplied text exactly unchanged.

Return only the resulting text. Do not add explanations, headings, commentary, markdown wrappers, quotation marks, labels, or reasoning."""

COMMENTARY_PREFIX = re.compile(
    r"^\s*(?:here(?:'s| is)|corrected(?: text)?|revised(?: text)?|"
    r"revision|output|result)\s*[:\-]",
    re.IGNORECASE,
)
NEWLINE_PATTERN = re.compile(r"\r\n|\r|\n")


def normalize_matches(
    payload: dict[str, Any], text: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get("matches", [])):
        if not isinstance(raw, dict):
            continue
        offset = raw.get("offset")
        length = raw.get("length")
        if (
            not isinstance(offset, int)
            or not isinstance(length, int)
            or offset < 0
            or length < 0
            or offset + length > len(text)
        ):
            continue
        rule = raw.get("rule") if isinstance(raw.get("rule"), dict) else {}
        category = (
            rule.get("category")
            if isinstance(rule.get("category"), dict)
            else {}
        )
        replacements: list[str] = []
        for replacement in raw.get("replacements", []):
            value = (
                replacement.get("value")
                if isinstance(replacement, dict)
                else None
            )
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
        key=lambda item: (
            item["offset"],
            -item["length"],
            item["match_index"],
        ),
    )


def apply_edits(source: str, edits: list[dict[str, Any]]) -> str:
    output = source
    for edit in sorted(edits, key=lambda item: item["offset"], reverse=True):
        start = edit["offset"]
        output = (
            output[:start]
            + edit["replacement"]
            + output[start + edit["length"] :]
        )
    return output


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
    policy_group = RULE_POLICY.get(match_item["rule_id"])
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
    if len(replacements) == 1:
        selected = replacements[0]
        reason = "single_token_spelling_candidate"
    else:
        approved = SAFE_LEXICAL_REPLACEMENTS.get(original.casefold())
        selected = next(
            (
                candidate
                for candidate in replacements
                if approved is not None and candidate.casefold() == approved
            ),
            None,
        )
        if selected is None:
            return None, "multiple_replacements_without_approved_choice"
        reason = "approved_lexical_choice"
    if not selected.isalpha():
        return None, "replacement_not_single_word"
    if _case_pattern(original) != _case_pattern(selected):
        return None, "replacement_changes_case_pattern"
    return selected, reason


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
    started = time.perf_counter()
    decisions: list[dict[str, Any]] = []
    provisional: dict[int, dict[str, Any]] = {}
    for match_item in matches:
        replacement, reason = select_safe_replacement(match_item)
        decision = {
            "match_index": match_item["match_index"],
            "rule_id": match_item["rule_id"],
            "category_id": match_item["category_id"],
            "policy_group": RULE_POLICY.get(
                match_item["rule_id"], "UNCLASSIFIED"
            ),
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
        item for item in matches if item["actionable_replacements"]
    ]
    for decision in decisions:
        if not decision["accepted"]:
            continue
        candidate = provisional[decision["match_index"]]
        if any(
            other["match_index"] != candidate["match_index"]
            and edits_overlap(candidate, other)
            for other in actionable
        ):
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
    return apply_edits(source, edits), decisions, time.perf_counter() - started


def compact_evidence(
    matches: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    decision_by_index = {
        decision["match_index"]: decision for decision in decisions
    }
    evidence: list[dict[str, Any]] = []
    for match_item in matches:
        decision = decision_by_index[match_item["match_index"]]
        policy_group = decision["policy_group"]
        escalation_class: str | None = None
        if (
            policy_group == AMBIGUOUS
            and match_item["rule_id"] not in AMBIGUOUS_NON_GRAMMAR_RULES
        ):
            escalation_class = "ambiguous_grammar"
        elif (
            policy_group == SAFE
            and not decision["accepted"]
            and decision["reason"] in CONTEXTUAL_SAFE_REJECTION_REASONS
        ):
            escalation_class = "contextual_spelling"
        if escalation_class is not None:
            evidence.append(
                {
                    "match_index": match_item["match_index"],
                    "escalation_class": escalation_class,
                    "rule_id": match_item["rule_id"],
                    "category_id": match_item["category_id"],
                    "message": match_item["message"],
                    "short_message": match_item["short_message"],
                    "offset": match_item["offset"],
                    "length": match_item["length"],
                    "original_text": match_item["original_text"],
                    "replacement_candidates": match_item["replacements"][:8],
                    "safe_rejection_reason": decision["reason"],
                }
            )
    return evidence


def route_post_safe(
    post_matches: list[dict[str, Any]],
    post_decisions: list[dict[str, Any]],
    safe_correction_count: int,
) -> dict[str, Any]:
    evidence = compact_evidence(post_matches, post_decisions)
    if evidence:
        classes = {item["escalation_class"] for item in evidence}
        if safe_correction_count:
            reason = (
                "safe_partial_with_unresolved_grammar"
                if "ambiguous_grammar" in classes
                else "safe_partial_with_contextual_spelling"
            )
        elif "ambiguous_grammar" in classes:
            reason = "remaining_ambiguous_grammar"
        else:
            reason = "remaining_contextual_spelling"
        return {"route_to_gemma": True, "reason": reason, "evidence": evidence}
    if not post_matches:
        reason = (
            "safe_resolved_all_actionable_evidence"
            if safe_correction_count
            else "clean_no_meaningful_evidence"
        )
        return {"route_to_gemma": False, "reason": reason, "evidence": []}
    policy_groups = {
        decision["policy_group"] for decision in post_decisions
    }
    if policy_groups and policy_groups <= {IGNORE}:
        reason = "ignore_only_evidence"
    elif any(
        decision["policy_group"] == SAFE and decision["accepted"]
        for decision in post_decisions
    ):
        reason = "post_safe_deterministic_evidence_not_escalated"
    else:
        reason = "non_escalatable_evidence"
    return {"route_to_gemma": False, "reason": reason, "evidence": []}


def build_gemma_instruction(evidence: list[dict[str, Any]]) -> str:
    lines = [HYBRID_PROMPT, "", "Unresolved LanguageTool evidence:"]
    for index, item in enumerate(evidence, 1):
        replacements = ", ".join(
            repr(value) for value in item["replacement_candidates"]
        )
        lines.append(
            f"{index}. rule={item['rule_id']} category={item['category_id']} "
            f"span={item['offset']}:{item['offset'] + item['length']} "
            f"text={item['original_text']!r} message={item['message']!r} "
            f"candidates=[{replacements}]"
        )
    return "\n".join(lines)


def formatting_signature(value: str) -> dict[str, Any]:
    lines = value.splitlines()
    return {
        "newline_count": value.count("\n"),
        "trailing_newline": value.endswith("\n"),
        "blank_line_indexes": [
            index for index, line in enumerate(lines) if not line
        ],
        "line_prefixes": [
            line[
                : len(line)
                - len(line.lstrip(" \t-*\u20220123456789."))
            ]
            for line in lines
        ],
    }


def newline_tokens(value: str) -> list[str]:
    return NEWLINE_PATTERN.findall(value)


def changed_opcodes(source: str, output: str) -> list[dict[str, Any]]:
    matcher = difflib.SequenceMatcher(a=source, b=output, autojunk=False)
    return [
        {
            "tag": tag,
            "source_start": source_start,
            "source_end": source_end,
            "output_start": output_start,
            "output_end": output_end,
            "source_text": source[source_start:source_end],
            "output_text": output[output_start:output_end],
        }
        for (
            tag,
            source_start,
            source_end,
            output_start,
            output_end,
        ) in matcher.get_opcodes()
        if tag != "equal"
    ]


def evidence_windows(
    source: str, evidence: list[dict[str, Any]], radius: int = 40
) -> list[dict[str, int]]:
    windows: list[dict[str, int]] = []
    for item in evidence:
        offset = item["offset"]
        match_end = offset + item["length"]
        previous = max(
            source.rfind(marker, 0, offset)
            for marker in ("\n", ". ", "? ", "! ")
        )
        sentence_start = (
            0
            if previous < 0
            else previous + (1 if source[previous] == "\n" else 2)
        )
        following = [
            position
            for position in (
                source.find("\n", match_end),
                source.find(". ", match_end),
                source.find("? ", match_end),
                source.find("! ", match_end),
            )
            if position >= 0
        ]
        sentence_end = min(following) + 1 if following else len(source)
        windows.append(
            {
                "start": max(sentence_start, offset - radius),
                "end": min(sentence_end, match_end + radius),
            }
        )
    return windows


def opcode_is_local(
    opcode: dict[str, Any], windows: list[dict[str, int]]
) -> bool:
    start = opcode["source_start"]
    end = opcode["source_end"]
    if start == end:
        return any(
            window["start"] <= start <= window["end"] for window in windows
        )
    return any(
        window["start"] <= start and end <= window["end"]
        for window in windows
    )


def validate_gemma_output(
    source: str,
    output: str | None,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    windows = evidence_windows(source, evidence)
    if output is None:
        return {
            "accepted": False,
            "rejection_reasons": ["missing_output"],
            "changed_opcodes": [],
            "changed_character_budget": 0,
            "evidence_windows": windows,
            "latency_seconds": time.perf_counter() - started,
        }
    reasons: list[str] = []
    if not output or not output.strip():
        reasons.append("empty_output")
    if "\x00" in output:
        reasons.append("null_byte")
    if "```" not in source and "```" in output:
        reasons.append("markdown_wrapper")
    if not source.lstrip().startswith("# ") and output.lstrip().startswith("# "):
        reasons.append("markdown_wrapper")
    if COMMENTARY_PREFIX.match(output) and not COMMENTARY_PREFIX.match(source):
        reasons.append("commentary_or_label")
    if newline_tokens(source) != newline_tokens(output):
        reasons.append("newline_structure_changed")
    if formatting_signature(source) != formatting_signature(output):
        reasons.append("formatting_structure_changed")
    source_length = len(source)
    output_length = len(output)
    if source_length >= 20 and output_length < source_length * 0.65:
        reasons.append("possible_truncation")
    if abs(output_length - source_length) > max(
        20, math.ceil(source_length * 0.25)
    ):
        reasons.append("extreme_length_change")
    opcodes = changed_opcodes(source, output)
    changed_budget = sum(
        max(
            opcode["source_end"] - opcode["source_start"],
            opcode["output_end"] - opcode["output_start"],
        )
        for opcode in opcodes
    )
    maximum_changed = max(
        16, math.ceil(source_length * 0.25), len(evidence) * 12
    )
    if changed_budget > maximum_changed:
        reasons.append("excessive_character_changes")
    if len(opcodes) > max(4, len(evidence) * 3):
        reasons.append("excessive_edit_segments")
    if opcodes and not windows:
        reasons.append("edit_without_unresolved_evidence")
    elif any(not opcode_is_local(opcode, windows) for opcode in opcodes):
        reasons.append("edit_outside_evidence_window")
    for item in evidence:
        candidates = item["replacement_candidates"]
        if (
            item["escalation_class"] != "contextual_spelling"
            or len(candidates) < 2
        ):
            continue
        candidate_only_outputs = {
            apply_edits(
                source,
                [
                    {
                        "offset": item["offset"],
                        "length": item["length"],
                        "replacement": candidate,
                    }
                ],
            )
            for candidate in candidates
        }
        if output in candidate_only_outputs:
            reasons.append(
                "ambiguous_spelling_candidate_without_contextual_resolution"
            )
    return {
        "accepted": not reasons,
        "rejection_reasons": list(dict.fromkeys(reasons)),
        "changed_opcodes": opcodes,
        "changed_character_budget": changed_budget,
        "maximum_changed_character_budget": maximum_changed,
        "evidence_windows": windows,
        "latency_seconds": time.perf_counter() - started,
    }
