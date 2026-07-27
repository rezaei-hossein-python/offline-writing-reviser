from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.hybrid_service import (
    HybridProofreadingService,
)
from offline_writing_reviser.proofreading.policy import (
    detect_language_quality_signals,
    route_post_safe,
    validate_gemma_output,
)
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.providers.ollama import OllamaInferenceResult
from offline_writing_reviser.provisioning import (
    AIProvisioner,
    ProvisioningCancelled,
)


class LanguageTool:
    def __init__(self, payloads=None):
        self.payloads = list(payloads or [])
        self.calls = []

    def check(self, text):
        self.calls.append(text)
        payload = self.payloads.pop(0) if self.payloads else {"matches": []}
        return payload, 0.001


class Provider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def revise_with_telemetry(self, text, instruction, timeout_seconds):
        self.calls.append((text, instruction))
        return OllamaInferenceResult(self.output, {})

    def runtime_diagnostics(self, timeout_seconds=2.0):
        return {"acceleration": "cpu"}


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (
            "I made a decision to not attend the meeting because I was not "
            "feeling good.",
            "I decided not to attend the meeting because I wasn't feeling well.",
        ),
        (
            "I am writing this email for informing you about the issue.",
            "I am writing this email to inform you about the issue.",
        ),
        ("He explained me the process.", "He explained the process to me."),
        (
            "The meeting was very good and we discussed about many important "
            "things.",
            "The meeting went very well, and we discussed many important issues.",
        ),
    ],
)
def test_larger_meaning_preserving_proofreading_is_accepted(source, candidate):
    signals = detect_language_quality_signals(source)
    result = validate_gemma_output(source, candidate, [], signals)

    assert signals
    assert result["accepted"] is True


@pytest.mark.parametrize(
    ("source", "candidate", "reason"),
    [
        (
            "Jordan Lee approved CAD 1,250 on July 8, 2026.",
            "Jordan Lee approved CAD 1,500 on July 8, 2026.",
            "numbers_not_preserved",
        ),
        (
            "Build API-42 may ship after 3:30 PM.",
            "Build API-42 must ship after 3:30 PM.",
            "modality_not_preserved",
        ),
        (
            "Do not email ops@example.com before Friday.",
            "Email ops@example.com before Friday.",
            "negation_not_preserved",
        ),
        (
            "Can Microsoft deploy release-7.2?",
            "Microsoft can deploy release-7.2.",
            "question_structure_changed",
        ),
        (
            'The log said “retry=false”.',
            'The log said “retry=true”.',
            "quotes_not_preserved",
        ),
        (
            "I am writing to inform you about the issue.",
            "I am writing to inform you about an issue.",
            "reference_not_preserved",
        ),
    ],
)
def test_semantic_guard_rejects_protected_meaning_drift(
    source, candidate, reason
):
    result = validate_semantic_preservation(source, candidate)

    assert result.accepted is False
    assert reason in result.reasons


def test_expletive_there_is_not_misdetected_as_a_name():
    result = validate_semantic_preservation(
        "There is many reasons to wait.",
        "There are many reasons to wait.",
    )

    assert result.accepted is True


def test_naturalness_signal_routes_without_languagetool_evidence():
    source = "He explained me the process."
    routing = route_post_safe([], [], 0, source)

    assert routing["route_to_gemma"] is True
    assert routing["reason"] == "language_quality_signal"
    assert routing["quality_signals"] == ["explain_object"]


def test_correct_natural_text_stays_on_fast_languagetool_path():
    source = "The report is ready for review."
    provider = Provider("This must not be used.")
    service = HybridProofreadingService(
        provider=provider,
        language_tool=LanguageTool([{"matches": []}, {"matches": []}]),
    )

    result = service.revise(source)

    assert result.revised_text == source
    assert provider.calls == []
    assert result.metadata["gemma_routed"] == 0


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "I am writing this email for informing you about the issue.",
            "I am writing this email to inform you about the issue.",
        ),
        (
            "We discussed about the budget.",
            "We discussed the budget.",
        ),
        ("I am agree with the proposal.", "I agree with the proposal."),
        ("Please return back the form.", "Please return the form."),
        (
            "Kindly revert back to me by Monday.",
            "Kindly get back to me by Monday.",
        ),
        (
            "I made a decision to not attend the meeting because I was not "
            "feeling good.",
            "I decided not to attend the meeting because I was not feeling well.",
        ),
    ],
)
def test_high_confidence_idiom_fixes_use_fast_deterministic_path(
    source, expected
):
    provider = Provider("This must not be used.")
    service = HybridProofreadingService(
        provider=provider,
        language_tool=LanguageTool([{"matches": []}, {"matches": []}]),
    )

    result = service.revise(source)

    assert result.revised_text == expected
    assert provider.calls == []
    assert result.metadata["deterministic_language_correction_count"] >= 1


def test_service_accepts_material_naturalness_improvement():
    source = "He explained me the process."
    candidate = "He explained the process to me."
    provider = Provider(candidate)
    service = HybridProofreadingService(
        provider=provider,
        language_tool=LanguageTool(
            [{"matches": []}, {"matches": []}, {"matches": []}]
        ),
    )

    result = service.revise(source)

    assert result.revised_text == candidate
    assert result.metadata["gemma_accepted"] == 1


def test_service_falls_back_when_candidate_changes_a_fact():
    source = "I have a strong doubt that build API-42 may work."
    provider = Provider("I am certain that build API-43 must work.")
    service = HybridProofreadingService(
        provider=provider,
        language_tool=LanguageTool(
            [{"matches": []}, {"matches": []}]
        ),
    )

    result = service.revise(source)

    assert result.revised_text == source
    assert result.metadata["gemma_fallback"] == 1
    assert "identifiers_not_preserved" in result.metadata[
        "gemma_rejection_reasons"
    ]


class ExistingProvider:
    def resolved_executable(self):
        return "ollama.exe"

    def ensure_api_running(self, timeout_seconds):
        return None


class Model:
    def __init__(self, installed):
        self.provider = ExistingProvider()
        self.installed = installed
        self.pulled = 0

    def model_installed(self):
        return self.installed

    def pull_model(self, progress, **kwargs):
        self.pulled += 1
        progress("pulling manifest", 5, 10)


def test_ai_provisioner_reuses_existing_ollama_and_model(tmp_path):
    model = Model(installed=True)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    updates = []

    provisioner.provision(lambda *values: updates.append(values))

    assert model.pulled == 0
    assert updates[-1] == ("gemma3:4b is already installed", 1, 1)


def test_ai_provisioner_retries_only_missing_model(tmp_path):
    model = Model(installed=False)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )

    provisioner.provision(lambda *_values: None)

    assert model.pulled == 1


def test_ollama_download_keeps_partial_file_on_cancel(monkeypatch, tmp_path):
    class MissingProvider(ExistingProvider):
        def resolved_executable(self):
            raise OfflineWritingProviderUnavailable("missing")

    model = Model(installed=False)
    model.provider = MissingProvider()
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )

    class Response:
        status = 200
        headers = {"Content-Length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b"1234"

    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    checks = iter([False, True])

    with pytest.raises(ProvisioningCancelled):
        provisioner.download_ollama(
            lambda *_values: None,
            cancelled=lambda: next(checks),
        )

    assert (tmp_path / "OllamaSetup.exe.part").read_bytes() == b"1234"


def test_ollama_download_offline_failure_can_retry_and_resume(
    monkeypatch, tmp_path
):
    model = Model(installed=False)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    partial = tmp_path / "OllamaSetup.exe.part"
    partial.write_bytes(b"1234")
    requests = []

    class Response:
        status = 206
        headers = {"Content-Length": "4"}

        def __init__(self):
            self.chunks = iter([b"5678", b""])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return next(self.chunks)

    outcomes = iter(
        [
            urllib.error.URLError("offline"),
            Response(),
        ]
    )

    def urlopen(request, **_kwargs):
        requests.append(request)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        urlopen,
    )
    with pytest.raises(OfflineWritingProviderUnavailable):
        provisioner.download_ollama(
            lambda *_values: None, cancelled=lambda: False
        )

    destination = provisioner.download_ollama(
        lambda *_values: None, cancelled=lambda: False
    )

    assert destination.read_bytes() == b"12345678"
    assert requests[-1].get_header("Range") == "bytes=4-"


def test_installer_never_waits_for_optional_ai_setup():
    installer = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")

    provision_line = next(
        line for line in installer.splitlines() if "--provision-model" in line
        and line.startswith("Filename:")
    )
    assert "postinstall" in provision_line
    assert "nowait" in provision_line
    assert "waituntilterminated" not in provision_line
    assert "EnsureOllamaInstalled" not in installer
