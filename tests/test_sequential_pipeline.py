from __future__ import annotations

import logging

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.errors import (
    OfflineWritingCorrectionUnavailable,
)
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.sequential import (
    PARAPHRASING_MODEL,
    SequentialWritingService,
    should_invoke_paraphraser,
    split_production_sections,
    split_sequential_sections,
)
from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionResult,
    LanguageToolEdit,
    LanguageToolFailure,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


class FakeCorrectionService:
    def __init__(self, results: dict[str, LanguageToolCorrectionResult] | None = None):
        self.results = results or {}
        self.calls: list[str] = []

    def correct(self, text: str) -> LanguageToolCorrectionResult:
        self.calls.append(text)
        return self.results.get(text, correction_result(text, text))


class FakeProvider:
    provider_name = "ollama_cli"
    model_identifier = PARAPHRASING_MODEL

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, float]] = []
        self.cancelled = False

    def revise(self, text, instruction, timeout_seconds):
        self.calls.append((text, instruction, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def cancel_current(self):
        self.cancelled = True

    def is_available(self):
        return True


def edit(
    original: str,
    replacement: str,
    *,
    offset: int = 0,
    category: str = "GRAMMAR",
    issue_type: str = "grammar",
    rule_id: str = "TEST_RULE",
) -> LanguageToolEdit:
    return LanguageToolEdit(
        offset=offset,
        length=len(original),
        original=original,
        replacement=replacement,
        rule_id=rule_id,
        category=category,
        issue_type=issue_type,
        message="synthetic test edit",
    )


def correction_result(
    original: str,
    corrected: str,
    *edits: LanguageToolEdit,
    failure: LanguageToolFailure | None = None,
) -> LanguageToolCorrectionResult:
    return LanguageToolCorrectionResult(
        original_text=original,
        corrected_text=corrected,
        applied_edits=tuple(edits),
        skipped_edits=(),
        rule_ids=tuple(item.rule_id for item in edits),
        categories=tuple(item.category for item in edits),
        duration_ms=12.5,
        runtime_status="unavailable" if failure else "ready",
        error_status=failure.code if failure else None,
        failure=failure,
    )


def service(corrections=None, responses=None, *, chunk_characters=1000):
    provider = FakeProvider(responses)
    correction = FakeCorrectionService(corrections)
    instance = SequentialWritingService(
        provider,
        correction,
        OfflineWritingConfig(chunk_characters=chunk_characters),
    )
    return instance, correction, provider


@pytest.mark.parametrize(
    ("source", "corrected", "category", "issue_type"),
    [
        ("I recieved the adress yesterday.", "I received the address yesterday.", "TYPOS", "misspelling"),
        ("hello world", "Hello world.", "CASING", "typographical"),
        ("He go to work every day.", "He goes to work every day.", "GRAMMAR", "grammar"),
        ("We discussed about the project.", "We discussed the project.", "COLLOCATIONS", "grammar"),
    ],
)
def test_mechanical_fast_paths_skip_qwen(source, corrected, category, issue_type):
    synthetic_edit = edit(
        source,
        corrected,
        category=category,
        issue_type=issue_type,
    )
    instance, correction, provider = service(
        {source: correction_result(source, corrected, synthetic_edit)}
    )

    result = instance.revise(source)

    assert result.revised_text == corrected
    assert correction.calls == [source]
    assert provider.calls == []
    assert result.metadata["qwen_invoked"] is False
    assert result.metadata["result_category"] == "languagetool_only"


def test_already_natural_text_is_unchanged_without_qwen():
    source = "The meeting starts at nine tomorrow morning."
    instance, _correction, provider = service()

    result = instance.revise(source)

    assert result.revised_text == source
    assert provider.calls == []
    assert result.metadata["result_category"] == "unchanged"


@pytest.mark.parametrize(
    ("source", "response", "expected"),
    [
        (
            "The meeting was very good and we discussed many important things.",
            "The meeting was productive, and we discussed several important topics.",
            "The meeting was productive, and we discussed several important topics.",
        ),
        (
            "I am writing this email for informing you about the issue.",
            "I am writing this email to inform you about the issue.",
            "I am writing this email to inform you about the issue.",
        ),
    ],
)
def test_awkward_text_invokes_qwen_with_checkpoint3_prompt(source, response, expected):
    instance, correction, provider = service(responses=[response])

    result = instance.revise(source)

    assert correction.calls == [source]
    assert provider.calls[0][0] == source
    assert provider.calls[0][1] == REVISION_INSTRUCTION
    assert result.revised_text == expected
    assert result.metadata["qwen_call_count"] == 1
    assert result.metadata["qwen_accepted_sections"] == 1


def test_qwen_receives_languagetool_corrected_text_and_all_forms_are_retained():
    source = "I recieved a very good report and we discussed many important things."
    corrected = "I received a very good report, and we discussed many important things."
    revised = "I received a strong report, and we discussed several important topics."
    edits = (
        edit("recieved", "received", offset=2, category="TYPOS", issue_type="misspelling"),
        edit("report", "report,", offset=23, category="PUNCTUATION", issue_type="typographical"),
    )
    instance, _correction, provider = service(
        {source: correction_result(source, corrected, *edits)},
        [revised],
    )

    result = instance.revise(source)

    assert provider.calls[0][0] == corrected
    assert result.original_text == source
    assert result.languagetool_text == corrected
    assert result.paraphrased_text == revised
    assert result.final_text == revised


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (OfflineWritingProviderTimeout("late"), "qwen_timeout"),
        (OfflineWritingProviderUnavailable("missing"), "qwen_unavailable"),
    ],
)
def test_qwen_runtime_failure_falls_back_to_languagetool(failure, reason):
    source = "I recieved a very good report and we discussed many important things."
    corrected = "I received a very good report, and we discussed many important things."
    instance, _correction, provider = service(
        {
            source: correction_result(
                source,
                corrected,
                edit("recieved", "received", category="TYPOS", issue_type="misspelling"),
                edit("report", "report,", category="PUNCTUATION", issue_type="typographical"),
            )
        },
        [failure],
    )

    result = instance.revise(source)

    assert len(provider.calls) == 1
    assert result.revised_text == corrected
    assert result.metadata["fallback_sections"] == 1
    assert result.metadata["qwen_rejected_sections"] == 1
    assert reason in {"qwen_timeout", "qwen_unavailable"}


@pytest.mark.parametrize(
    "unsafe",
    [
        "Here is the revised text: This changes everything.",
        "Broken\x00 output.",
        "The meeting was very good and we discussed 19 important things.",
    ],
)
def test_malformed_or_unsafe_qwen_output_falls_back_without_partial_text(unsafe):
    source = "The meeting was very good and we discussed many important things."
    instance, _correction, provider = service(responses=[unsafe])

    result = instance.revise(source)

    assert len(provider.calls) == 1
    assert result.revised_text == source
    assert unsafe not in result.revised_text
    assert result.metadata["qwen_rejected_sections"] == 1


def test_qwen_no_change_or_markdown_line_spacing_uses_languagetool_result():
    source = "At this point in time, we are currently reviewing the request."
    unchanged, _correction, _provider = service(responses=[source])
    unchanged_result = unchanged.revise(source)
    assert unchanged_result.revised_text == source
    assert unchanged_result.metadata["qwen_accepted_sections"] == 0
    assert unchanged_result.metadata["fallback_sections"] == 1

    paragraphs = "The summary is longer than it needs to be.\n\nRegards,\nCarlos"
    spaced = "The summary is longer than it needs to be.  \n\nRegards,  \nCarlos"
    spacing, _correction, _provider = service(responses=[spaced])
    spacing_result = spacing.revise(paragraphs)
    assert spacing_result.revised_text == paragraphs
    assert spacing_result.metadata["qwen_accepted_sections"] == 0


@pytest.mark.parametrize(
    ("source", "unsafe"),
    [
        (
            "I am writing this email for informing you that the meeting with Microsoft is on September 15 at 9:30 AM and costs $125.",
            "I am writing this email to inform you that the meeting with Microsoft is on September 16 at 9:30 AM and costs $125.",
        ),
        ("I do not approve this request for informing you.", "I approve this request."),
        ("We may postpone the meeting for informing you.", "We will postpone the meeting."),
        ("Could you send the report before Friday for informing me?", "Send the report after Friday."),
    ],
)
def test_original_text_validation_protects_facts_negation_modality_and_questions(source, unsafe):
    instance, _correction, _provider = service(responses=[unsafe])

    result = instance.revise(source)

    assert result.revised_text == source
    assert result.metadata["qwen_rejected_sections"] == 1


def test_one_failed_section_does_not_discard_other_languagetool_corrections():
    first = "I recieved the adress yesterday."
    second = "The meeting was very good and we discussed many important things."
    source = first + "\n\n" + second
    corrected_first = "I received the address yesterday."
    corrections = {
        first: correction_result(
            first,
            corrected_first,
            edit(first, corrected_first, category="TYPOS", issue_type="misspelling"),
        ),
        second: correction_result(second, second),
    }
    instance, correction, provider = service(
        corrections,
        ["Here is the revised text: The meeting was productive."],
        chunk_characters=70,
    )

    result = instance.revise(source)

    assert correction.calls == [first, second]
    assert len(provider.calls) == 1
    assert result.revised_text == corrected_first + "\n\n" + second
    assert result.metadata["fallback_sections"] == 1


def test_languagetool_failure_is_explicit_and_qwen_is_not_used():
    source = "Text for informing you."
    failure = LanguageToolFailure("java_missing", "Bundled Java is missing", True)
    instance, _correction, provider = service(
        {source: correction_result(source, source, failure=failure)},
        ["Should not run"],
    )

    with pytest.raises(OfflineWritingCorrectionUnavailable):
        instance.revise(source)

    assert provider.calls == []


def test_progress_is_quiet_on_fast_path_and_accessible_for_qwen_and_fallback():
    fast, _correction, _provider = service()
    fast_progress: list[str] = []
    fast.revise("The meeting starts at nine tomorrow morning.", fast_progress.append)
    assert fast_progress == []

    model, _correction, _provider = service(
        responses=[OfflineWritingProviderUnavailable("offline")]
    )
    model_progress: list[str] = []
    model.revise(
        "I am writing this email for informing you about the issue.",
        model_progress.append,
    )
    assert model_progress == [
        "Revising text",
        "AI paraphrasing unavailable; text unchanged",
    ]


def test_privacy_safe_summary_contains_metrics_but_not_text(caplog):
    source = "I am writing this email for informing you about SECRET-9988."
    instance, _correction, _provider = service(
        responses=["I am writing this email to inform you about SECRET-9988."]
    )

    with caplog.at_level(logging.INFO, logger="offline-writing-reviser"):
        result = instance.revise(source)

    assert result.metadata["languagetool_duration_ms"] == 12.5
    assert result.metadata["qwen_invoked"] is True
    assert "input_chars=" in caplog.text
    assert "lt_duration_ms=" in caplog.text
    assert "qwen_invoked=True" in caplog.text
    assert "SECRET-9988" not in caplog.text


def test_paragraph_grouping_preserves_source_and_protected_boundaries():
    text = (
        "Email ops@example.com about API-42 on September 15.\n\n"
        "Second paragraph remains intact.\n\n"
        "Third paragraph remains intact."
    )
    sections = split_sequential_sections(text, 80)

    assert "".join(sections) == text
    assert all("ops@example.com" not in section or "API-42" in section for section in sections)


def test_production_sections_keep_paragraph_fallbacks_independent():
    text = "First awkward paragraph.\r\n\r\nSecond paragraph.\r\n\r\nThird paragraph."

    sections = split_production_sections(text, 1000)

    assert len(sections) == 3
    assert "".join(sections) == text


def test_fast_path_rule_defaults_uncertain_multiple_grammar_edits_to_qwen():
    source = "Text."
    correction = correction_result(
        source,
        source,
        edit("a", "b"),
        edit("c", "d"),
    )
    assert should_invoke_paraphraser(source, correction) is True
