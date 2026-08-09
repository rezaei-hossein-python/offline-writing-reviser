from __future__ import annotations

from dataclasses import replace

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.providers.base import OfflineWritingProviderError
from offline_writing_reviser.provisioning import (
    AIProvisioner,
    ModelProvisioner,
    PREVIOUS_OFFICIAL_MODEL,
    provisioning_start_required,
)
from offline_writing_reviser.provisioning_state import (
    ProvisioningPhase,
    ProvisioningSnapshot,
    ProvisioningStateStore,
)


class FakeProvider:
    def resolved_executable(self):
        return "ollama.exe"

    def ensure_api_running(self, timeout_seconds):
        return None


class MigrationModel:
    def __init__(self, models):
        self.provider = FakeProvider()
        self.models = set(models)
        self.pull_calls = 0
        self.inference_calls = 0
        self.end_to_end_calls = 0
        self.removed = []
        self.fail_pull = False
        self.fail_inference = False
        self.fail_end_to_end = False
        self.fail_removal = False

    def model_installed(self, model=None):
        return (model or "qwen3:1.7b") in self.models

    def pull_model(self, progress, **_kwargs):
        self.pull_calls += 1
        if self.fail_pull:
            raise OfflineWritingProviderError("download failed")
        self.models.add("qwen3:1.7b")
        progress("pulling qwen3:1.7b", 10, 10)

    def verify_inference(self, timeout_seconds):
        self.inference_calls += 1
        if self.fail_inference:
            raise OfflineWritingProviderError("inference failed")

    def verify_end_to_end(self):
        self.end_to_end_calls += 1
        if self.fail_end_to_end:
            raise OfflineWritingProviderError("semantic validation failed")

    def model_size(self, model):
        return 3_333_000_000 if model == PREVIOUS_OFFICIAL_MODEL else None

    def remove_previous_official_model(self, model):
        assert model == PREVIOUS_OFFICIAL_MODEL
        if self.fail_removal:
            raise OfflineWritingProviderError("removal failed")
        self.models.remove(model)
        self.removed.append(model)


class SettingsRecorder:
    def __init__(self):
        self.saved = []

    def save(self, config):
        self.saved.append(config)
        return config


def provisioner(model, settings=None):
    previous = OfflineWritingConfig(model=PREVIOUS_OFFICIAL_MODEL)
    return AIProvisioner(
        replace(previous, model="qwen3:1.7b"),
        model_provisioner=model,
        settings_store=settings,
        previous_config=previous,
    )


def test_existing_qwen_is_verified_before_exact_gemma_removal():
    model = MigrationModel(
        {"qwen3:1.7b", PREVIOUS_OFFICIAL_MODEL, "user-model:latest"}
    )
    settings = SettingsRecorder()

    result = provisioner(model, settings).provision(lambda *_args: None)

    assert model.pull_calls == 0
    assert model.inference_calls == 2
    assert model.end_to_end_calls == 2
    assert model.removed == [PREVIOUS_OFFICIAL_MODEL]
    assert model.models == {"qwen3:1.7b", "user-model:latest"}
    assert settings.saved[-1].model == "qwen3:1.7b"
    assert result.removed_model == PREVIOUS_OFFICIAL_MODEL
    assert result.recovered_bytes == 3_333_000_000


def test_upgrade_downloads_qwen_once_then_verifies_and_removes_gemma():
    model = MigrationModel({PREVIOUS_OFFICIAL_MODEL, "unrelated:2b"})

    result = provisioner(model).provision(lambda *_args: None)

    assert model.pull_calls == 1
    assert result.removed_model == PREVIOUS_OFFICIAL_MODEL
    assert model.models == {"qwen3:1.7b", "unrelated:2b"}


def test_fresh_install_downloads_only_qwen_and_does_not_remove_anything():
    model = MigrationModel(set())

    result = provisioner(model).provision(lambda *_args: None)

    assert model.pull_calls == 1
    assert model.models == {"qwen3:1.7b"}
    assert model.removed == []
    assert result.removed_model is None


@pytest.mark.parametrize(
    "failure",
    ["fail_pull", "fail_inference", "fail_end_to_end"],
)
def test_verification_failure_retains_gemma_and_previous_configuration(failure):
    model = MigrationModel({PREVIOUS_OFFICIAL_MODEL})
    setattr(model, failure, True)
    settings = SettingsRecorder()

    with pytest.raises(OfflineWritingProviderError):
        provisioner(model, settings).provision(lambda *_args: None)

    assert PREVIOUS_OFFICIAL_MODEL in model.models
    assert model.removed == []
    assert settings.saved == []


def test_removal_failure_restores_gemma_configuration_and_retry_succeeds():
    model = MigrationModel({"qwen3:1.7b", PREVIOUS_OFFICIAL_MODEL})
    model.fail_removal = True
    settings = SettingsRecorder()
    worker = provisioner(model, settings)

    with pytest.raises(OfflineWritingProviderError, match="removal failed"):
        worker.provision(lambda *_args: None)

    assert PREVIOUS_OFFICIAL_MODEL in model.models
    assert [item.model for item in settings.saved] == [
        "qwen3:1.7b",
        PREVIOUS_OFFICIAL_MODEL,
    ]
    model.fail_removal = False
    worker.provision(lambda *_args: None)
    assert model.pull_calls == 0
    assert PREVIOUS_OFFICIAL_MODEL not in model.models


def test_model_provisioner_refuses_non_exact_model_removal():
    worker = ModelProvisioner(OfflineWritingConfig())

    with pytest.raises(OfflineWritingProviderError, match="exact previous"):
        worker.remove_previous_official_model("gemma3:4b-copy")


def test_migration_result_persists_recovered_space(tmp_path):
    store = ProvisioningStateStore(tmp_path / "state.json")
    snapshot = ProvisioningSnapshot(
        phase=ProvisioningPhase.READY,
        ready=True,
        removed_model=PREVIOUS_OFFICIAL_MODEL,
        recovered_bytes=3_333_000_000,
    )

    store.save(snapshot)

    loaded = store.load()
    assert loaded.removed_model == PREVIOUS_OFFICIAL_MODEL
    assert loaded.recovered_bytes == 3_333_000_000


def test_upgrade_setup_restarts_even_when_old_state_is_ready():
    previous = OfflineWritingConfig(model=PREVIOUS_OFFICIAL_MODEL)
    target = replace(previous, model="qwen3:1.7b")
    snapshot = ProvisioningSnapshot(
        phase=ProvisioningPhase.READY,
        ready=True,
    )

    assert provisioning_start_required(snapshot, previous, target) is True
    assert provisioning_start_required(snapshot, target, target) is False
