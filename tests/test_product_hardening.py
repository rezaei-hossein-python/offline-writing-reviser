from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace

import pytest

from offline_writing_reviser.application import (
    BackgroundCoordinator,
    execute_control_command,
)
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
from offline_writing_reviser.windows.control import ControlCommand
from offline_writing_reviser.windows.control import (
    WindowsControlServer,
    send_control_command,
)
from offline_writing_reviser.settings_ui import ACCESSIBLE_CONTROLS, SettingsWindow


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

    assert loaded.model == "gemma3:4b"
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


def test_explicit_saved_model_is_not_overwritten_by_new_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "model": "llama3.2:3b",
                "timeout_seconds": 45,
                "max_characters": 4000,
                "hotkey": "Ctrl+Alt+W",
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsStore(
        path,
        defaults=OfflineWritingConfig(log_file=tmp_path / "app.log"),
    ).load()

    assert loaded.model == "llama3.2:3b"


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


def test_missing_model_state_transition_in_background_coordinator(
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
    coordinator = BackgroundCoordinator(
        config=config,
        settings_store=SettingsStore(
            tmp_path / "settings.json", defaults=config
        ),
        runtime=runtime,
        stop_event=threading.Event(),
        logger=logging.getLogger("test"),
    )
    states = []
    monkeypatch.setattr(coordinator, "discover_models", lambda: ["other:latest"])
    monkeypatch.setattr(coordinator, "set_state", states.append)

    coordinator.refresh_status()

    assert states == [ApplicationState.MODEL_UNAVAILABLE]


def test_actionable_error_dialogs_are_rate_limited(monkeypatch, tmp_path):
    config = OfflineWritingConfig(log_file=tmp_path / "app.log")
    runtime = type(
        "Runtime",
        (),
        {"controller": StubController(), "has_registered_hotkeys": True},
    )()
    coordinator = BackgroundCoordinator(
        config=config,
        settings_store=SettingsStore(tmp_path / "settings.json", defaults=config),
        runtime=runtime,
        stop_event=threading.Event(),
        logger=logging.getLogger("test"),
    )
    shown = []
    monkeypatch.setattr(coordinator.settings_window, "show_error", shown.append)
    message = user_message_for_error("no_selection")

    coordinator.present_error(message)
    coordinator.present_error(message)

    assert shown == [message]


def test_existing_instance_control_command_does_not_spawn(monkeypatch):
    import offline_writing_reviser.application as application

    spawned = []
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application, "send_control_command", lambda command: True)
    monkeypatch.setattr(
        application, "_start_background_process", lambda: spawned.append(True)
    )

    assert execute_control_command(ControlCommand.SETTINGS) == 0
    assert spawned == []


def test_settings_command_starts_background_then_opens_settings(monkeypatch):
    import offline_writing_reviser.application as application

    sent = []
    monkeypatch.setattr(application.sys, "platform", "win32")

    def send(command):
        sent.append(command)
        return len(sent) > 1

    monkeypatch.setattr(
        application,
        "send_control_command",
        send,
    )
    monkeypatch.setattr(application, "_start_background_process", lambda: None)
    monkeypatch.setattr(application, "wait_for_control_server", lambda: True)

    assert execute_control_command(ControlCommand.SETTINGS) == 0
    assert sent == [ControlCommand.SETTINGS, ControlCommand.SETTINGS]


def test_exit_command_is_successful_when_background_is_not_running(
    monkeypatch, capsys
):
    import offline_writing_reviser.application as application

    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application, "send_control_command", lambda command: False)

    assert execute_control_command(ControlCommand.EXIT) == 0
    assert "not running" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argument", "command"),
    [
        ("--settings", ControlCommand.SETTINGS),
        ("--exit", ControlCommand.EXIT),
        ("--restart", ControlCommand.RESTART),
    ],
)
def test_control_command_entry_points(monkeypatch, argument, command):
    import offline_writing_reviser.__main__ as main_module

    calls = []
    monkeypatch.setattr(main_module, "_hide_private_console", lambda: None)
    monkeypatch.setattr(
        main_module,
        "execute_control_command",
        lambda value: calls.append(value) or 0,
    )

    assert main_module.main([argument]) == 0
    assert calls == [command]


def test_normal_startup_source_has_no_tray_dependency():
    source = __import__("pathlib").Path(
        "src/offline_writing_reviser/application.py"
    ).read_text(encoding="utf-8")
    package_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in __import__("pathlib").Path(
            "src/offline_writing_reviser"
        ).rglob("*.py")
    )

    assert "TrayIcon" not in source
    assert "pystray" not in package_sources


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows IPC")
def test_hidden_control_window_dispatches_commands_and_stops():
    settings = threading.Event()
    exiting = threading.Event()
    restarting = threading.Event()
    server = WindowsControlServer(
        on_settings=settings.set,
        on_exit=exiting.set,
        on_restart=restarting.set,
    )

    try:
        server.start()
        assert server.is_running is True
        assert __import__("ctypes").windll.user32.IsWindowVisible(
            server._window_handle
        ) == 0
        assert send_control_command(ControlCommand.SETTINGS) is True
        assert send_control_command(ControlCommand.EXIT) is True
        assert send_control_command(ControlCommand.RESTART) is True
        assert settings.wait(timeout=1)
        assert exiting.wait(timeout=1)
        assert restarting.wait(timeout=1)
    finally:
        server.stop()

    assert server.is_running is False
    assert send_control_command(ControlCommand.SETTINGS) is False


def test_closing_settings_does_not_request_background_shutdown():
    stop_event = threading.Event()
    settings = SettingsWindow(
        config_getter=OfflineWritingConfig,
        save_callback=lambda config: config,
        reset_callback=OfflineWritingConfig,
        model_loader=list,
    )

    class FakeWindow:
        def __init__(self):
            self.destroyed = False

        def destroy(self):
            self.destroyed = True

    window = FakeWindow()
    settings._window = window

    settings._close_window()

    assert window.destroyed is True
    assert settings._window is None
    assert stop_event.is_set() is False


def test_settings_accessibility_contract_has_names_roles_and_logical_tab_order():
    assert [control.tab_order for control in ACCESSIBLE_CONTROLS] == list(
        range(1, len(ACCESSIBLE_CONTROLS) + 1)
    )
    assert all(control.name.strip() for control in ACCESSIBLE_CONTROLS)
    assert all(control.role.strip() for control in ACCESSIBLE_CONTROLS)
    assert {control.name for control in ACCESSIBLE_CONTROLS} == {
        "Model",
        "Refresh installed models",
        "Revision timeout",
        "Maximum input length",
        "Global hotkey",
        "Log location",
        "Reset to defaults",
        "Save settings",
        "Cancel",
    }


def test_settings_validation_routes_focus_to_relevant_control():
    assert SettingsWindow._validation_control(
        "Revision timeout must be between 5 and 600 seconds."
    ) == "timeout"
    assert SettingsWindow._validation_control(
        "Maximum input length must be between 100 and 100,000."
    ) == "maximum"
    assert SettingsWindow._validation_control(
        "Hotkey must use Ctrl and/or Alt."
    ) == "hotkey"
    assert SettingsWindow._validation_control(
        "Select a valid installed Ollama model."
    ) == "model"


def test_application_wires_hidden_control_to_clean_shutdown(
    monkeypatch, tmp_path
):
    import offline_writing_reviser.application as application

    lifecycle = []
    stop_event = threading.Event()

    class FakeRuntime:
        has_registered_hotkeys = True
        controller = StubController()

        def stop(self):
            lifecycle.append("runtime_stopped")

    class FakeInstance:
        def acquire(self):
            return True

        def release(self):
            lifecycle.append("instance_released")

    class FakeSettingsWindow:
        def show(self):
            lifecycle.append("settings_shown")

    class FakeBackground:
        def __init__(self, **_kwargs):
            self.settings_window = FakeSettingsWindow()

        def start(self):
            lifecycle.append("background_started")

        def run(self):
            lifecycle.append("background_run")

        def stop(self):
            lifecycle.append("background_stopped")

    class FakeControlServer:
        def __init__(self, **callbacks):
            self.callbacks = callbacks

        def start(self):
            lifecycle.append("control_started")
            self.callbacks["on_exit"]()

        def stop(self):
            lifecycle.append("control_stopped")

    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application, "_install_shutdown_handlers", lambda _event: None)
    monkeypatch.setattr(
        application,
        "start_offline_writing_runtime",
        lambda config, logger=None: FakeRuntime(),
    )
    monkeypatch.setattr(application, "BackgroundCoordinator", FakeBackground)
    app = application.OfflineWritingReviserApplication(
        config=OfflineWritingConfig(log_file=tmp_path / "app.log"),
        stop_event=stop_event,
        instance=FakeInstance(),
        control_server_factory=FakeControlServer,
    )

    assert app.run() == 0
    assert lifecycle == [
        "background_started",
        "control_started",
        "background_run",
        "control_stopped",
        "background_stopped",
        "runtime_stopped",
        "instance_released",
    ]


def test_packaged_restart_resets_pyinstaller_environment(monkeypatch):
    import offline_writing_reviser.application as application

    calls = []
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        application.sys, "executable", r"C:\App\OfflineWritingReviser.exe"
    )
    monkeypatch.setattr(
        application.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    application._start_background_process()

    assert calls[0][0] == [r"C:\App\OfflineWritingReviser.exe"]
    assert calls[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


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
