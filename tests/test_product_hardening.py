from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from offline_writing_reviser.application import DesktopCoordinator
from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.desktop_status import (
    ApplicationState,
    user_message_for_error,
)
from offline_writing_reviser.paths import resource_path
from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.settings import SettingsStore, SettingsValidationError
from offline_writing_reviser.version import __version__
from offline_writing_reviser.windows.controller import (
    OfflineWritingController,
    OfflineWritingRuntime,
)


@pytest.fixture(autouse=True)
def clear_settings_environment(monkeypatch):
    for name in (
        "OWR_MODEL",
        "OWR_HOTKEY",
        "OWR_TIMEOUT_SECONDS",
        "OWR_MAX_CHARACTERS",
        "OWR_OLLAMA_EXECUTABLE",
        "OWR_LOG_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_settings_are_sensible(tmp_path):
    defaults = OfflineWritingConfig(log_file=tmp_path / "app.log")
    loaded = SettingsStore(tmp_path / "settings.json", defaults=defaults).load()

    assert loaded.model == "llama3.2:3b"
    assert loaded.timeout_seconds == 45.0
    assert loaded.max_characters == 4000
    assert loaded.hotkey == "Ctrl+Alt+W"


def test_settings_save_and_load_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    defaults = OfflineWritingConfig(log_file=tmp_path / "app.log")
    store = SettingsStore(path, defaults=defaults)
    expected = replace(
        defaults,
        model="qwen2.5:3b",
        timeout_seconds=90,
        max_characters=8000,
        hotkey="Ctrl+Alt+R",
    )

    store.save(expected)
    loaded = store.load()

    assert loaded == expected
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "hotkey": "Ctrl+Alt+R",
        "max_characters": 8000,
        "model": "qwen2.5:3b",
        "timeout_seconds": 90.0,
    }
    assert not path.with_suffix(".json.tmp").exists()


def test_corrupt_settings_are_preserved_and_defaults_recovered(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ definitely-not-json", encoding="utf-8")
    defaults = OfflineWritingConfig(log_file=tmp_path / "app.log")
    store = SettingsStore(path, defaults=defaults)

    loaded = store.load()

    assert loaded == defaults
    assert store.recovered_corrupt_file is True
    assert path.with_suffix(".json.corrupt").read_text(encoding="utf-8") == (
        "{ definitely-not-json"
    )


def test_unmodified_global_hotkey_is_rejected(tmp_path):
    store = SettingsStore(
        tmp_path / "settings.json",
        defaults=OfflineWritingConfig(log_file=tmp_path / "app.log"),
    )

    with pytest.raises(SettingsValidationError):
        store.save(replace(store.defaults, hotkey="W"))


class StubController:
    def __init__(self):
        self.service = None
        self.triggered = 0

    def trigger(self):
        self.triggered += 1


class StubHotkeyManager:
    def __init__(self, registered_count=1):
        self.registered_count = registered_count
        self.stopped = 0

    def start(self):
        pass

    def stop(self):
        self.stopped += 1


def test_hotkey_setting_change_replaces_manager_safely():
    old_manager = StubHotkeyManager()
    candidate = StubHotkeyManager()
    controller = StubController()
    runtime = OfflineWritingRuntime(
        old_manager,
        controller=controller,
        config=OfflineWritingConfig(),
        hotkey_manager_factory=lambda **_kwargs: candidate,
    )
    changed = replace(runtime.config, hotkey="Ctrl+Alt+R")

    assert runtime.apply_config(changed) is True
    assert runtime.hotkey_manager is candidate
    assert old_manager.stopped == 1
    assert runtime.config.hotkey == "Ctrl+Alt+R"


def test_failed_hotkey_change_preserves_previous_working_manager():
    old_manager = StubHotkeyManager()
    rejected = StubHotkeyManager(registered_count=0)
    runtime = OfflineWritingRuntime(
        old_manager,
        controller=StubController(),
        config=OfflineWritingConfig(),
        hotkey_manager_factory=lambda **_kwargs: rejected,
    )

    assert runtime.apply_config(replace(runtime.config, hotkey="Ctrl+Alt+R")) is False
    assert runtime.hotkey_manager is old_manager
    assert runtime.config.hotkey == "Ctrl+Alt+W"
    assert old_manager.stopped == 0
    assert rejected.stopped == 1


def test_local_model_discovery_parses_and_sorts(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama

    provider = ollama.OllamaCliOfflineWritingProvider("llama3.2:3b")
    monkeypatch.setattr(provider, "_resolve_executable", lambda: "ollama")
    monkeypatch.setattr(
        provider,
        "_run",
        lambda *_args, **_kwargs: __import__("subprocess").CompletedProcess(
            ["ollama", "list"],
            0,
            stdout=(
                "NAME ID SIZE MODIFIED\n"
                "qwen2.5:3b 123 2GB now\n"
                "llama3.2:3b 456 2GB now\n"
            ),
            stderr="",
        ),
    )

    assert provider.list_installed_models() == ["llama3.2:3b", "qwen2.5:3b"]


@pytest.mark.parametrize(
    ("error", "state", "title"),
    [
        (
            OfflineWritingProviderUnavailable("missing"),
            ApplicationState.OLLAMA_UNAVAILABLE,
            "Ollama unavailable",
        ),
        (
            OfflineWritingModelMissing("missing"),
            ApplicationState.MODEL_UNAVAILABLE,
            "Configured model unavailable",
        ),
        (
            OfflineWritingProviderTimeout("timeout"),
            ApplicationState.ERROR,
            "Revision timed out",
        ),
    ],
)
def test_errors_map_to_user_facing_messages_without_internal_details(
    error, state, title
):
    message = user_message_for_error(error)

    assert message.state is state
    assert message.title == title
    assert "Traceback" not in message.message


class FakeService:
    def revise(self, _text):
        return type("Result", (), {"revised_text": "Revised."})()


class FakeAdapter:
    def capture(self):
        return type("Capture", (), {"text": "Original."})()

    def replace(self, _capture, _replacement):
        return True


def test_successful_revision_changes_state_without_notification_spam():
    states = []
    notifications = []
    controller = OfflineWritingController(
        FakeService(),
        FakeAdapter(),
        state_callback=states.append,
        notification_callback=notifications.append,
    )

    controller._run_revision()

    assert states == [ApplicationState.REVISING, ApplicationState.READY]
    assert notifications == []


def test_no_selection_has_one_actionable_notification():
    notifications = []
    controller = OfflineWritingController(
        FakeService(),
        type("EmptyAdapter", (), {"capture": lambda self: None})(),
        notification_callback=notifications.append,
    )

    controller._run_revision()

    assert len(notifications) == 1
    assert notifications[0].title == "No text selected"


def test_missing_model_state_transition_in_desktop_coordinator(
    monkeypatch, tmp_path
):
    config = OfflineWritingConfig(log_file=tmp_path / "app.log")
    runtime = type(
        "Runtime",
        (),
        {
            "controller": StubController(),
            "has_registered_hotkeys": True,
        },
    )()
    coordinator = DesktopCoordinator(
        config=config,
        settings_store=SettingsStore(
            tmp_path / "settings.json", defaults=config
        ),
        runtime=runtime,
        stop_event=__import__("threading").Event(),
        logger=logging.getLogger("test"),
    )
    states = []
    notices = []
    monkeypatch.setattr(coordinator, "discover_models", lambda: ["other:latest"])
    monkeypatch.setattr(coordinator, "set_state", states.append)
    monkeypatch.setattr(coordinator, "notify", notices.append)

    coordinator.refresh_status()

    assert states == [ApplicationState.MODEL_UNAVAILABLE]
    assert notices[0].state is ApplicationState.MODEL_UNAVAILABLE


def test_resource_path_uses_pyinstaller_bundle_root(monkeypatch, tmp_path):
    import offline_writing_reviser.paths as paths

    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_path("assets/app.ico") == tmp_path / "assets" / "app.ico"


def test_version_command_reports_release_version(capsys):
    from offline_writing_reviser.__main__ import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ == "0.2.0"
    assert "0.2.0" in capsys.readouterr().out
