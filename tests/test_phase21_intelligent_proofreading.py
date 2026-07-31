from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderError,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.provisioning import (
    AIProvisioner,
    ModelProvisioner,
    ProvisioningCancelled,
)


class Provider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def is_available(self):
        return True

    def revise(self, text, instruction, timeout_seconds):
        self.calls.append((text, instruction, timeout_seconds))
        return self.output


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
            "The meeting went very well, and we discussed many important things.",
        ),
    ],
)
def test_larger_meaning_preserving_revision_is_accepted(source, candidate):
    result = validate_semantic_preservation(source, candidate)
    assert result.accepted is True


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
            'The log said "retry=false".',
            'The log said "retry=true".',
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


def test_service_accepts_material_naturalness_improvement():
    source = "He explained me the process."
    candidate = "He explained the process to me."
    result = OfflineWritingService(Provider(candidate)).revise(source)
    assert result.revised_text == candidate


@pytest.mark.parametrize(
    ("source", "unsafe"),
    [
        (
            "The meeting is on September 15 at 9:30 AM and costs $125.",
            "The meeting is on September 16 at 10:30 AM and costs $150.",
        ),
        (
            "I do not approve this request.",
            "I approve this request.",
        ),
        (
            "Email ops@example.com about API-42.",
            "Email sales@example.com about API-43.",
        ),
    ],
)
def test_service_returns_original_when_candidate_changes_protected_fact(
    source, unsafe
):
    result = OfflineWritingService(Provider(unsafe)).revise(source)
    assert result.revised_text == source


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


def test_model_pull_reports_streamed_progress_and_verifies_installation(
    monkeypatch,
):
    class PullProvider:
        def __init__(self):
            self.model_checks = 0

        def api_models(self, timeout_seconds):
            self.model_checks += 1
            return [] if self.model_checks == 1 else ["gemma3:4b"]

    class Response:
        def __enter__(self):
            return iter(
                [
                    b'{"status":"pulling manifest"}\n',
                    b'{"status":"downloading","completed":5,"total":10}\n',
                    b'{"status":"success","completed":10,"total":10}\n',
                ]
            )

        def __exit__(self, *_args):
            return None

    provider = PullProvider()
    provisioner = ModelProvisioner(
        OfflineWritingConfig(), provider=provider
    )
    updates = []
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    assert provisioner.model_installed() is False
    provisioner.pull_model(lambda *values: updates.append(values))

    assert updates == [
        ("pulling manifest", None, None),
        ("downloading", 5, 10),
        ("success", 10, 10),
    ]
    assert provider.model_checks == 2


def test_model_pull_rejects_success_stream_without_installed_model(monkeypatch):
    class MissingModelProvider:
        def api_models(self, timeout_seconds):
            return []

    class Response:
        def __enter__(self):
            return iter([b'{"status":"success"}\n'])

        def __exit__(self, *_args):
            return None

    provisioner = ModelProvisioner(
        OfflineWritingConfig(), provider=MissingModelProvider()
    )
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(
        OfflineWritingProviderError,
        match="without installing the required model",
    ):
        provisioner.pull_model(lambda *_values: None)


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

    outcomes = iter([urllib.error.URLError("offline"), Response()])

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


def test_completed_partial_download_recovers_from_range_not_satisfiable(
    monkeypatch, tmp_path
):
    model = Model(installed=False)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    partial = tmp_path / "OllamaSetup.exe.part"
    partial.write_bytes(b"stale-complete-file")
    requests = []

    class Response:
        status = 200
        headers = {"Content-Length": "5"}

        def __init__(self):
            self.chunks = iter([b"fresh", b""])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return next(self.chunks)

    outcomes = iter(
        [
            urllib.error.HTTPError(
                "https://ollama.com/download/OllamaSetup.exe",
                416,
                "range not satisfiable",
                None,
                None,
            ),
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

    destination = provisioner.download_ollama(
        lambda *_values: None, cancelled=lambda: False
    )

    assert destination.read_bytes() == b"fresh"
    assert requests[0].get_header("Range") is not None
    assert requests[1].get_header("Range") is None


def test_installer_never_waits_for_model_setup():
    installer = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")
    provision_line = next(
        line
        for line in installer.splitlines()
        if "--provision-model" in line and line.startswith("Filename:")
    )
    assert "postinstall" in provision_line
    assert "nowait" in provision_line
    assert "waituntilterminated" not in provision_line
