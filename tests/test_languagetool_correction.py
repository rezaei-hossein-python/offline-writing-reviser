from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from offline_writing_reviser.correction import languagetool
from offline_writing_reviser.correction.languagetool import (
    LANGUAGE,
    LanguageToolClient,
    LanguageToolCorrectionService,
    LanguageToolRuntime,
    LanguageToolRuntimeError,
)


class PayloadRuntime:
    def __init__(self, matches):
        self.matches = matches

    def check(self, _text):
        return {"matches": self.matches}, 1.0


def match(
    offset,
    length,
    replacement,
    *,
    rule_id="TEST_RULE",
    category="GRAMMAR",
    issue_type="grammar",
):
    return {
        "offset": offset,
        "length": length,
        "message": "Test correction",
        "replacements": [{"value": replacement}],
        "rule": {
            "id": rule_id,
            "issueType": issue_type,
            "category": {"id": category, "name": category.title()},
        },
    }


def test_result_retains_original_corrected_edits_and_rule_metadata():
    service = LanguageToolCorrectionService(
        PayloadRuntime([match(3, 2, "goes", rule_id="HE_VERB_AGR")])
    )

    result = service.correct("He go to work.")

    assert result.original_text == "He go to work."
    assert result.corrected_text == "He goes to work."
    assert result.changed is True
    assert len(result.applied_edits) == 1
    assert result.skipped_edits == ()
    assert result.rule_ids == ("HE_VERB_AGR",)
    assert result.categories == ("GRAMMAR",)
    assert result.duration_ms >= 0
    assert result.runtime_status == "ready"
    assert result.error_status is None
    assert result.failure is None


def test_multiple_non_overlapping_edits_use_original_offsets_once():
    source = "I recieved the adress."
    service = LanguageToolCorrectionService(
        PayloadRuntime(
            [
                match(2, 8, "received", category="TYPOS", issue_type="misspelling"),
                match(15, 6, "address", category="TYPOS", issue_type="misspelling"),
            ]
        )
    )

    result = service.correct(source)

    assert result.corrected_text == "I received the address."
    assert len(result.applied_edits) == 2


def test_overlapping_edits_are_all_skipped():
    service = LanguageToolCorrectionService(
        PayloadRuntime([match(0, 8, "document"), match(0, 4, "dock")])
    )

    result = service.correct("documnet")

    assert result.corrected_text == "documnet"
    assert len(result.skipped_edits) == 2
    assert all(
        edit.reason == "overlapping_or_conflicting_edit"
        for edit in result.skipped_edits
    )


def test_conflicting_same_range_replacements_are_all_skipped():
    service = LanguageToolCorrectionService(
        PayloadRuntime([match(0, 8, "document"), match(0, 8, "documents")])
    )

    result = service.correct("documnet")

    assert not result.changed
    assert len(result.skipped_edits) == 2


@pytest.mark.parametrize(
    "bad_match",
    [
        {"offset": -1, "length": 2, "replacements": [{"value": "x"}]},
        {"offset": 999, "length": 2, "replacements": [{"value": "x"}]},
        {"offset": "0", "length": 2, "replacements": [{"value": "x"}]},
        {"offset": 0, "length": 2, "replacements": []},
        "not-a-match",
    ],
)
def test_malformed_matches_are_skipped_without_failure(bad_match):
    result = LanguageToolCorrectionService(PayloadRuntime([bad_match])).correct(
        "Keep this text."
    )

    assert result.corrected_text == "Keep this text."
    assert result.applied_edits == ()
    assert len(result.skipped_edits) == 1
    assert result.runtime_status == "ready"


@pytest.mark.parametrize(
    "source,replacement",
    [
        ("Jordan may approve 12 items.", "15"),
        ("The review is September 15, 2026.", "16"),
        ("The total is CAD 1,250.50.", "1,500.50"),
        ("Email ops@example.com.", "sales@example.com"),
        ("Visit https://example.com/API-42.", "https://example.com/API-43"),
        ("Do not cancel it.", ""),
    ],
)
def test_protected_token_changes_are_skipped(source, replacement):
    if "12" in source:
        original = "12"
    elif "September" in source:
        original = "15"
    elif "1,250.50" in source:
        original = "1,250.50"
    elif "ops@" in source:
        original = "ops@example.com"
    elif "API-42" in source:
        original = "https://example.com/API-42"
    else:
        original = "not "
    offset = source.index(original)
    service = LanguageToolCorrectionService(
        PayloadRuntime([match(offset, len(original), replacement)])
    )

    result = service.correct(source)

    assert result.corrected_text == source
    assert result.skipped_edits[0].reason.startswith("protected_tokens_changed:")


def test_capitalized_name_change_is_skipped_where_detectable():
    source = "Priya Raman may approve the change."
    offset = source.index("Raman")
    result = LanguageToolCorrectionService(
        PayloadRuntime(
            [match(offset, len("Raman"), "Roman", issue_type="misspelling")]
        )
    ).correct(source)

    assert result.corrected_text == source
    assert "names" in result.skipped_edits[0].reason


def test_style_suggestion_is_skipped_without_a_rule_policy_table():
    result = LanguageToolCorrectionService(
        PayloadRuntime(
            [match(0, 6, "Kindly", category="STYLE", issue_type="style")]
        )
    ).correct("Please send it.")

    assert result.corrected_text == "Please send it."
    assert result.skipped_edits[0].reason == "non_mechanical_suggestion"


def test_demonstrated_harmful_article_rule_is_skipped():
    source = "She is engineer at our office."
    payload = match(7, 8, "engineered", rule_id="BEEN_PART_AGREEMENT")
    payload["replacements"].append({"value": "engineering"})

    result = LanguageToolCorrectionService(PayloadRuntime([payload])).correct(source)

    assert result.corrected_text == source
    assert result.skipped_edits[0].reason == "demonstrated_unsafe_rule"


def test_utf16_offsets_are_converted_without_splitting_astral_characters():
    source = "😀 He go."
    result = LanguageToolCorrectionService(
        PayloadRuntime([match(6, 2, "goes")])
    ).correct(source)

    assert result.corrected_text == "😀 He goes."


def test_runtime_failure_is_returned_as_structured_information():
    class FailedRuntime:
        def check(self, _text):
            raise LanguageToolRuntimeError("missing", code="java_missing")

    result = LanguageToolCorrectionService(FailedRuntime()).correct("Text.")

    assert result.corrected_text == "Text."
    assert result.runtime_status == "unavailable"
    assert result.error_status == "java_missing"
    assert result.failure is not None
    assert result.failure.recoverable is True


def test_client_posts_explicit_english_and_rejects_malformed_response(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"matches": []}).encode()

    def urlopen(request, timeout):
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(languagetool.urllib.request, "urlopen", urlopen)
    payload, _ = LanguageToolClient("http://127.0.0.1:1234", 2.0).check("Text")

    assert payload == {"matches": []}
    assert f"language={LANGUAGE}".encode() in captured["data"]
    assert captured["timeout"] == 2.0

    Response.read = lambda _self: b'{"wrong": []}'
    with pytest.raises(LanguageToolRuntimeError) as caught:
        LanguageToolClient("http://127.0.0.1:1234").check("Text")
    assert caught.value.code == "malformed_response"


def test_private_runtime_uses_javaw_dynamic_port_reuses_and_stops_owned_process(
    monkeypatch, tmp_path
):
    javaw = tmp_path / "javaw.exe"
    jar = tmp_path / "languagetool-server.jar"
    javaw.write_bytes(b"javaw")
    jar.write_bytes(b"jar")
    captured = {"commands": []}

    class Process:
        pid = 321
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

    def popen(command, **kwargs):
        captured["commands"].append(command)
        captured["kwargs"] = kwargs
        return process

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(languagetool.subprocess, "Popen", popen)
    monkeypatch.setattr(
        languagetool.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )
    runtime = LanguageToolRuntime(javaw, jar, startup_timeout_seconds=1)

    runtime.start()
    first_url = runtime.base_url
    runtime.start()

    assert len(captured["commands"]) == 1
    command = captured["commands"][0]
    assert command[0] == str(javaw.resolve())
    assert "java.exe" not in command[0].casefold()
    assert "--port" in command
    assert first_url is not None and first_url.startswith("http://127.0.0.1:")
    assert captured["kwargs"]["creationflags"] == getattr(
        subprocess, "CREATE_NO_WINDOW", 0x08000000
    )
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
    runtime.stop()
    assert process.returncode == 0
    assert runtime.is_running is False
    with pytest.raises(LanguageToolRuntimeError) as caught:
        runtime.start()
    assert caught.value.code == "runtime_stopped"


def test_bundled_runtime_defaults_are_private_and_never_use_system_java():
    root = Path(__file__).resolve().parents[1]
    assert languagetool.default_javaw_path() == (
        root / "vendor" / "java" / "bin" / "javaw.exe"
    )
    assert languagetool.default_server_jar_path() == (
        root / "vendor" / "languagetool" / "languagetool-server.jar"
    )


def test_failed_warmup_stops_the_owned_process(monkeypatch, tmp_path):
    javaw = tmp_path / "javaw.exe"
    jar = tmp_path / "languagetool-server.jar"
    javaw.write_bytes(b"javaw")
    jar.write_bytes(b"jar")

    class Process:
        pid = 654
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

        def kill(self):
            self.returncode = -9

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    process = Process()
    monkeypatch.setattr(languagetool.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(
        languagetool.urllib.request,
        "urlopen",
        lambda *_a, **_k: ReadyResponse(),
    )
    monkeypatch.setattr(
        LanguageToolClient,
        "check",
        lambda *_a, **_k: (_ for _ in ()).throw(
            LanguageToolRuntimeError("warmup failed", code="request_timeout")
        ),
    )
    runtime = LanguageToolRuntime(javaw, jar, startup_timeout_seconds=1)

    with pytest.raises(LanguageToolRuntimeError):
        runtime.warmup()

    assert process.returncode == 0
    assert runtime.is_running is False
