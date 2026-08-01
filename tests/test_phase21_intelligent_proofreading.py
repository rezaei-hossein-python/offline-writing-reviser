from __future__ import annotations

import threading
import urllib.error
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.errors import OfflineWritingMalformedOutput
from offline_writing_reviser.core.prompt import REVISION_INSTRUCTION
from offline_writing_reviser.core.sanitizer import sanitize_revision_output
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
    ProvisioningController,
    send_provisioning_show_command,
    _format_progress,
    run_model_provisioning,
)
from offline_writing_reviser.provisioning_state import (
    ProvisioningPhase,
    ProvisioningStateStore,
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
        (
            "There is 3 issue in the report.",
            "There are 3 issues in the report.",
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
            "I received item 9 yesterday.",
            "I received item #9 yesterday.",
            "numbers_not_preserved",
        ),
        (
            "He go to office 50 every day.",
            "He goes to the office 50 times every day.",
            "number_context_changed",
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


def test_equivalent_negation_and_causal_phrasing_is_accepted():
    source = (
        "I made a decision to not attend the meeting because I was not feeling good."
    )
    candidate = "I decided not to attend the meeting due to feeling unwell."

    assert OfflineWritingService(Provider(candidate)).revise(source).revised_text == candidate

    causal_as = "I decided not to attend the meeting as I wasn't feeling well."
    assert OfflineWritingService(Provider(causal_as)).revise(source).revised_text == causal_as


def test_each_negated_clause_remains_protected():
    source = "I do not approve the request, and I do not reject the alternative."
    candidate = "I approve the request, and I do not reject the alternative."

    result = validate_semantic_preservation(source, candidate)

    assert result.accepted is False
    assert "negation_not_preserved" in result.reasons


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (
            "The meeting was very good and we discussed about many important things.",
            "The meeting went very well, and we discussed many important issues.",
        ),
        (
            "The proposal gives a very clear explanation of the plan.",
            "The proposal clearly explains the plan.",
        ),
        (
            "First, we reviewed the draft. Then, we approved the final plan.",
            "We approved the final plan after reviewing the draft.",
        ),
        (
            "The concise report clearly explains the issue.",
            "The concise report clearly explains the issue and makes the next steps easy to understand.",
        ),
        ("The meeting starts at nine tomorrow morning.",) * 2,
        ("I recieved the adress yesterday.", "I received the address yesterday."),
        ("He go to work every day.", "He goes to work every day."),
    ],
)
def test_service_accepts_broad_safe_revision_matrix(source, candidate):
    assert OfflineWritingService(Provider(candidate)).revise(source).revised_text == candidate


def test_semantic_guard_allows_sentence_reordering_across_selection():
    source = "We reviewed the draft. Then, we approved the plan."
    candidate = "We approved the plan after reviewing the draft."

    assert validate_semantic_preservation(source, candidate).accepted


@pytest.mark.parametrize(
    "output",
    [
        "Analysis: I corrected the grammar.",
        "# Revised text\nI received the address yesterday.",
        "**I received the address yesterday.**",
        '```text\nI received the address yesterday.\n```',
        '{"revised_text":"I received the address yesterday."}',
        "<revised>I received the address yesterday.</revised>",
        "",
        "You are an expert English editor. Return only the final revised text.",
    ],
)
def test_unusable_model_wrappers_are_rejected(output):
    with pytest.raises(OfflineWritingMalformedOutput):
        sanitize_revision_output(
            output, original_text="I recieved the adress yesterday."
        )


@pytest.mark.parametrize(
    "output",
    [
        "Revised text:\nI received the address yesterday.",
        '" I received the address yesterday. "',
    ],
)
def test_conservative_harmless_wrapper_extraction(output):
    assert sanitize_revision_output(
        output, original_text="I recieved the adress yesterday."
    ) == "I received the address yesterday."


def test_truncated_and_factually_deleted_output_is_rejected_by_service():
    source = (
        "The final report explains the budget risks and includes the approved "
        "mitigation plan for the launch."
    )
    candidate = "The final report explains the budget risks."

    assert OfflineWritingService(Provider(candidate)).revise(source).revised_text == source


def test_rejection_diagnostic_is_specific_and_does_not_log_selection(caplog):
    source = "Private selected wording that must never appear in logs."
    with caplog.at_level("WARNING", logger="offline-writing-reviser"):
        result = OfflineWritingService(Provider("Analysis: no change")).revise(source)

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert result.revised_text == source
    assert "rejection_reason=commentary" in log_text
    assert source not in log_text


def test_prompt_does_not_contain_obsolete_locality_restrictions():
    assert "minimal edit" not in REVISION_INSTRUCTION.casefold()
    assert "preserve every line break" not in REVISION_INSTRUCTION.casefold()


def test_service_preserves_original_when_number_role_changes():
    source = "He go to office 50 every day."
    candidate = "He goes to the office 50 times every day."

    result = OfflineWritingService(Provider(candidate)).revise(source)

    assert result.revised_text == source


@pytest.mark.parametrize(
    ("source", "candidate", "expected"),
    [
        (
            "He go to office 2 every day.",
            "He goes to Office 2 every day.",
            "He goes to office 2 every day.",
        ),
        (
            "We discussed about project API-3.",
            "We discussed Project API-3.",
            "We discussed project API-3.",
        ),
    ],
)
def test_service_preserves_unique_noninitial_word_casing(
    source, candidate, expected
):
    result = OfflineWritingService(Provider(candidate)).revise(source)
    assert result.revised_text == expected


def test_service_removes_new_number_sign_while_retaining_correction():
    source = "I recieved item 9 yesterday."
    candidate = "I received item #9 yesterday."

    result = OfflineWritingService(Provider(candidate)).revise(source)

    assert result.revised_text == "I received item 9 yesterday."


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
        self.verified = 0

    def model_installed(self):
        return self.installed

    def pull_model(self, progress, **kwargs):
        self.pulled += 1
        self.installed = True
        progress("pulling manifest", 5, 10)

    def verify_inference(self, timeout_seconds):
        self.verified += 1


def test_ai_provisioner_reuses_existing_ollama_and_model(tmp_path):
    model = Model(installed=True)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    updates = []
    provisioner.provision(lambda *values: updates.append(values))
    assert model.pulled == 0
    assert model.verified == 1
    assert updates[-1] == ("Intelligent revision is ready", 1, 1)


def test_ai_provisioner_retries_only_missing_model(tmp_path):
    model = Model(installed=False)
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    provisioner.provision(lambda *_values: None)
    assert model.pulled == 1
    assert model.verified == 1


def test_provisioning_is_not_complete_before_model_and_inference_verification(
    tmp_path,
):
    model = Model(installed=False)
    updates = []
    AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    ).provision(lambda *values: updates.append(values))

    assert updates[-2:] == [
        ("Testing minimal inference", None, None),
        ("Intelligent revision is ready", 1, 1),
    ]
    assert model.installed is True
    assert model.verified == 1


def test_provisioning_rejects_model_that_disappears_before_final_verification(
    tmp_path,
):
    class DisappearingModel(Model):
        def __init__(self):
            super().__init__(installed=True)
            self.checks = 0

        def model_installed(self):
            self.checks += 1
            return self.checks == 1

    model = DisappearingModel()
    with pytest.raises(OfflineWritingProviderError, match="not installed after setup"):
        AIProvisioner(
            OfflineWritingConfig(),
            model_provisioner=model,
            cache_directory=tmp_path,
        ).provision(lambda *_values: None)
    assert model.verified == 0


def test_clean_state_installs_ollama_then_pulls_and_verifies(tmp_path):
    events = []

    class CleanProvider:
        installed = False

        def resolved_executable(self):
            events.append("find_ollama")
            if not self.installed:
                raise OfflineWritingProviderUnavailable("not installed")
            return "ollama.exe"

        def ensure_api_running(self, timeout_seconds):
            events.append("api_ready")

    class CleanModel(Model):
        def __init__(self):
            super().__init__(installed=False)
            self.provider = CleanProvider()

        def model_installed(self):
            events.append("model_list")
            return self.installed

        def pull_model(self, progress, **kwargs):
            events.append("pull_model")
            super().pull_model(progress, **kwargs)

        def verify_inference(self, timeout_seconds):
            events.append("inference")
            super().verify_inference(timeout_seconds)

    class CleanProvisioner(AIProvisioner):
        def download_ollama(self, progress, **kwargs):
            events.append("download_ollama")
            return tmp_path / "OllamaSetup.exe"

        def install_ollama(self, installer):
            events.append("install_ollama")
            self.model.provider.installed = True

    model = CleanModel()
    CleanProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    ).provision(lambda *_values: None)

    assert events == [
        "find_ollama",
        "download_ollama",
        "install_ollama",
        "api_ready",
        "model_list",
        "pull_model",
        "model_list",
        "inference",
    ]


def test_interrupted_model_pull_retries_and_resumes_without_reinstall(tmp_path):
    class ResumableModel(Model):
        def __init__(self):
            super().__init__(installed=False)

        def pull_model(self, progress, **kwargs):
            self.pulled += 1
            progress("downloading layer", self.pulled * 5, 10)
            if self.pulled == 1:
                raise OfflineWritingProviderUnavailable(
                    "interrupted; Ollama retains completed layers"
                )
            self.installed = True

    model = ResumableModel()
    provisioner = AIProvisioner(
        OfflineWritingConfig(), model_provisioner=model, cache_directory=tmp_path
    )
    updates = []

    with pytest.raises(OfflineWritingProviderUnavailable, match="interrupted"):
        provisioner.provision(lambda *values: updates.append(values))
    assert model.installed is False
    assert model.verified == 0
    assert ("Intelligent revision is ready", 1, 1) not in updates

    provisioner.provision(lambda *_values: None)
    assert model.pulled == 2
    assert model.installed is True
    assert model.verified == 1


def test_model_progress_details_include_bytes_and_percentage():
    detail, percentage = _format_progress(5 * 1024 * 1024, 20 * 1024 * 1024)

    assert detail == "5.0 MB of 20.0 MB (25%)"
    assert percentage == 25


def test_shared_controller_persists_progress_and_prevents_duplicate_workers(
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvisioner:
        calls = 0

        def provision(self, progress, **_kwargs):
            self.calls += 1
            progress("downloading model layer", 1_500_000_000, 3_000_000_000)
            entered.set()
            assert release.wait(timeout=3)

    worker = BlockingProvisioner()
    store = ProvisioningStateStore(tmp_path / "state.json")
    controller = ProvisioningController(
        OfflineWritingConfig(), provisioner=worker, state_store=store
    )

    assert controller.start() is True
    assert entered.wait(timeout=1)
    assert controller.start() is False
    persisted = store.load()
    assert persisted.phase is ProvisioningPhase.DOWNLOADING_MODEL
    assert persisted.downloaded_bytes == 1_500_000_000
    assert persisted.total_bytes == 3_000_000_000
    assert persisted.percentage == 50
    assert persisted.active is True
    assert worker.calls == 1

    release.set()
    assert controller.wait()
    assert controller.snapshot.ready is True


def test_ready_and_failed_states_persist_outside_window(tmp_path):
    ready_store = ProvisioningStateStore(tmp_path / "ready.json")
    ready = ProvisioningController(
        OfflineWritingConfig(),
        provisioner=type(
            "ReadyProvisioner",
            (),
            {"provision": lambda self, progress, **kwargs: None},
        )(),
        state_store=ready_store,
    )
    ready.start()
    assert ready.wait()
    assert ProvisioningController(
        OfflineWritingConfig(), state_store=ready_store
    ).snapshot.phase is ProvisioningPhase.READY

    failed_store = ProvisioningStateStore(tmp_path / "failed.json")

    class FailedProvisioner:
        def provision(self, progress, **_kwargs):
            raise OfflineWritingProviderUnavailable("pull interrupted")

    failed = ProvisioningController(
        OfflineWritingConfig(),
        provisioner=FailedProvisioner(),
        state_store=failed_store,
    )
    failed.start()
    assert failed.wait()
    restored = ProvisioningController(
        OfflineWritingConfig(), state_store=failed_store
    ).snapshot
    assert restored.phase is ProvisioningPhase.FAILED
    assert restored.latest_error == "pull interrupted"
    assert restored.retry_available is True


def test_retry_resumes_failed_shared_controller(tmp_path):
    class RetryProvisioner:
        def __init__(self):
            self.calls = 0

        def provision(self, progress, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise OfflineWritingProviderUnavailable("temporary failure")
            progress("Verifying installed model", None, None)

    provisioner = RetryProvisioner()
    controller = ProvisioningController(
        OfflineWritingConfig(),
        provisioner=provisioner,
        state_store=ProvisioningStateStore(tmp_path / "state.json"),
    )
    assert controller.start()
    assert controller.wait()
    assert controller.snapshot.phase is ProvisioningPhase.FAILED
    assert controller.start()
    assert controller.wait()
    assert controller.snapshot.phase is ProvisioningPhase.READY
    assert provisioner.calls == 2


def test_start_menu_invocation_focuses_existing_setup(monkeypatch, tmp_path):
    releases = []
    focus_requests = []

    class ExistingInstance:
        def acquire(self):
            return False

        def release(self):
            releases.append(True)

    monkeypatch.setattr(
        "offline_writing_reviser.windows.single_instance.WindowsSingleInstance",
        lambda _name: ExistingInstance(),
    )
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.send_provisioning_show_command",
        lambda: focus_requests.append(True) or True,
    )

    assert run_model_provisioning(
        OfflineWritingConfig(log_file=tmp_path / "app.log")
    ) == 0
    assert focus_requests == [True]
    assert releases == [True]


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


def test_model_pull_cancellation_leaves_model_not_ready(monkeypatch):
    class MissingModelProvider:
        def __init__(self):
            self.model_checks = 0

        def api_models(self, timeout_seconds):
            self.model_checks += 1
            return []

    class Response:
        def __enter__(self):
            return iter(
                [
                    b'{"status":"downloading","completed":5,"total":10}\n',
                    b'{"status":"downloading","completed":6,"total":10}\n',
                ]
            )

        def __exit__(self, *_args):
            return None

    provider = MissingModelProvider()
    provisioner = ModelProvisioner(OfflineWritingConfig(), provider=provider)
    updates = []
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(ProvisioningCancelled, match="later retry can resume"):
        provisioner.pull_model(
            lambda *values: updates.append(values),
            cancelled=lambda: bool(updates),
        )

    assert updates == [("downloading", 5, 10)]
    assert provider.model_checks == 0


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


def test_setup_window_hides_without_cancelling_and_reopens_same_job(
    monkeypatch, tmp_path
):
    from PySide6 import QtCore, QtWidgets
    import offline_writing_reviser.provisioning as provisioning_module

    monkeypatch.setattr(
        provisioning_module,
        "PROVISIONING_MUTEX_NAME",
        r"Local\OfflineWritingReviserProvisioningReopenTest",
    )
    monkeypatch.setattr(
        provisioning_module,
        "PROVISIONING_CONTROL_WINDOW_CLASS",
        "OfflineWritingReviserProvisioningReopenTest",
    )
    monkeypatch.setattr(
        provisioning_module,
        "PROVISIONING_CONTROL_WINDOW_TITLE",
        "Offline Writing Reviser Provisioning Reopen Test",
    )
    monkeypatch.setattr(
        "offline_writing_reviser.windows.single_instance.WindowsSingleInstance",
        lambda _name: type(
            "TestInstance",
            (),
            {"acquire": lambda self: True, "release": lambda self: None},
        )(),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )

    release = threading.Event()
    started = threading.Event()
    observations = {
        "calls": 0,
        "cancelled": False,
        "hidden": False,
        "reopened": False,
        "ready_visible": False,
    }

    class BlockingProvisioner:
        def __init__(self, config):
            self.config = config

        def provision(self, progress, *, cancelled, **_kwargs):
            observations["calls"] += 1
            progress("downloading model layer", 1_500_000_000, 3_000_000_000)
            started.set()
            while not release.wait(timeout=0.01):
                if cancelled():
                    observations["cancelled"] = True
                    raise ProvisioningCancelled("cancelled")
            progress("Verifying installed model", None, None)
            progress("Testing minimal inference", None, None)

    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.AIProvisioner",
        BlockingProvisioner,
    )
    state_store = ProvisioningStateStore(tmp_path / "state.json")
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.ProvisioningStateStore",
        lambda: state_store,
    )
    announcements = []
    monkeypatch.setattr(
        "offline_writing_reviser.provisioning._announce_provisioning",
        lambda _target, text, _logger: announcements.append(text),
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    timer = QtCore.QTimer()
    step = {"value": 0, "ticks": 0}

    def advance_dialogs():
        step["ticks"] += 1
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible():
                widget.done(int(QtWidgets.QMessageBox.StandardButton.Yes))
                return
        setup = next(
            (
                widget
                for widget in app.topLevelWidgets()
                if widget.windowTitle() == "Offline Writing Reviser - AI Setup"
            ),
            None,
        )
        if setup is None:
            return
        if step["value"] == 0 and started.is_set() and setup.isVisible():
            setup.close()
            step["value"] = 1
            step["ticks"] = 0
            return
        if step["value"] == 1 and step["ticks"] >= 3:
            observations["hidden"] = not setup.isVisible()
            assert state_store.load().active is True
            assert send_provisioning_show_command() is True
            step["value"] = 2
            return
        if step["value"] == 2 and setup.isVisible():
            observations["reopened"] = True
            assert "downloading model layer" in setup.findChild(
                QtWidgets.QLabel, ""
            ).text() or state_store.load().percentage == 50
            release.set()
            step["value"] = 3
            return
        if step["value"] == 3 and state_store.load().ready:
            observations["ready_visible"] = setup.isVisible()
            for button in setup.findChildren(QtWidgets.QPushButton):
                if button.text() == "Close":
                    button.click()
                    return

    timer.timeout.connect(advance_dialogs)
    timer.start(20)
    try:
        exit_code = run_model_provisioning(
            OfflineWritingConfig(log_file=tmp_path / "app.log")
        )
    finally:
        timer.stop()
        release.set()

    assert exit_code == 0
    assert observations == {
        "calls": 1,
        "cancelled": False,
        "hidden": True,
        "reopened": True,
        "ready_visible": True,
    }
    assert "Offline Writing Reviser is ready." in announcements
    assert state_store.load().phase is ProvisioningPhase.READY
