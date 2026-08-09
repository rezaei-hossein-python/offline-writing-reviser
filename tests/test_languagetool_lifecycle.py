from __future__ import annotations

import subprocess
import threading
import time

import pytest

from offline_writing_reviser.correction import languagetool
from offline_writing_reviser.correction.languagetool import (
    READINESS_PROBE_TEXT,
    LanguageToolClient,
    LanguageToolRuntime,
    LanguageToolRuntimeError,
)


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class FakeProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.terminate_count = 0
        self.kill_count = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_count += 1
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode

    def kill(self):
        self.kill_count += 1
        self.returncode = -9


@pytest.fixture
def runtime_factory(monkeypatch, tmp_path):
    javaw = tmp_path / "javaw.exe"
    jar = tmp_path / "languagetool-server.jar"
    javaw.write_bytes(b"javaw")
    jar.write_bytes(b"jar")
    processes = []
    popen_kwargs = []
    ports = iter(range(41000, 41100))

    def popen(_command, **kwargs):
        process = FakeProcess(1000 + len(processes))
        processes.append(process)
        popen_kwargs.append(kwargs)
        return process

    monkeypatch.setattr(languagetool.subprocess, "Popen", popen)
    monkeypatch.setattr(languagetool, "_find_loopback_port", lambda: next(ports))
    monkeypatch.setattr(
        languagetool.socket,
        "create_connection",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    def make(**kwargs):
        startup_timeout_seconds = kwargs.pop("startup_timeout_seconds", 1)
        return LanguageToolRuntime(
            javaw,
            jar,
            startup_timeout_seconds=startup_timeout_seconds,
            **kwargs,
        )

    return make, processes, popen_kwargs


def _successful_check(_self, _text, *, timeout_seconds=None):
    return {"matches": []}, 1.0


def test_real_api_probe_must_finish_before_runtime_is_ready(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def check(_self, text, *, timeout_seconds=None):
        calls.append((text, timeout_seconds))
        entered.set()
        assert release.wait(timeout=2)
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()
    thread = threading.Thread(target=runtime.start)
    thread.start()
    assert entered.wait(timeout=1)

    assert runtime.status()["state"] == "starting"
    assert runtime.base_url is None
    assert calls[0][0] == READINESS_PROBE_TEXT
    assert calls[0][1] is not None

    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert runtime.status()["state"] == "ready"
    assert len(processes) == 1
    runtime.stop()


def test_concurrent_startup_callers_share_one_process(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    entered = threading.Event()
    release = threading.Event()

    def check(_self, _text, *, timeout_seconds=None):
        entered.set()
        assert release.wait(timeout=2)
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()
    threads = [threading.Thread(target=runtime.start) for _ in range(6)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=1)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(processes) == 1
    runtime.stop()


def test_hotkey_request_waits_for_background_startup(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    probe_entered = threading.Event()
    release_probe = threading.Event()
    user_result = []

    def check(_self, text, *, timeout_seconds=None):
        if text == READINESS_PROBE_TEXT:
            probe_entered.set()
            assert release_probe.wait(timeout=2)
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()
    runtime.start_in_background()
    assert probe_entered.wait(timeout=1)
    hotkey = threading.Thread(
        target=lambda: user_result.append(runtime.check("Hotkey text."))
    )
    hotkey.start()
    time.sleep(0.02)
    assert hotkey.is_alive()
    release_probe.set()
    hotkey.join(timeout=2)

    assert user_result == [({"matches": []}, 1.0)]
    assert len(processes) == 1
    runtime.stop()


def test_successful_background_startup_becomes_ready(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    monkeypatch.setattr(LanguageToolClient, "check", _successful_check)
    runtime = make()

    runtime.start_in_background()
    runtime._warm_thread.join(timeout=2)

    assert runtime.is_ready
    assert runtime.status()["state"] == "ready"
    assert len(processes) == 1
    runtime.stop()


def test_background_startup_timeout_restarts_once_and_becomes_ready(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory

    def connect(*_args, **_kwargs):
        if len(processes) == 1:
            raise OSError("first installed launch stalled")
        return FakeConnection()

    monkeypatch.setattr(languagetool.socket, "create_connection", connect)
    monkeypatch.setattr(LanguageToolClient, "check", _successful_check)
    runtime = make(startup_timeout_seconds=0.02)

    runtime.start_in_background()
    runtime._warm_thread.join(timeout=2)

    assert runtime.is_ready
    assert len(processes) == 2
    assert processes[0].terminate_count == 1
    assert runtime.process is processes[1]
    runtime.stop()


def test_rapid_correction_callers_are_serialized_on_one_process(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    def check(_self, _text, *, timeout_seconds=None):
        nonlocal active, maximum_active
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.01)
        with guard:
            active -= 1
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()
    threads = [
        threading.Thread(target=runtime.check, args=(f"Text {index}.",))
        for index in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1
    assert len(processes) == 1
    runtime.stop()


def test_repeated_and_idle_corrections_reuse_one_process(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    monkeypatch.setattr(LanguageToolClient, "check", _successful_check)
    runtime = make()

    runtime.warmup()
    pid = runtime.process.pid
    runtime.check("First.")
    runtime.check("Second.")
    time.sleep(0.02)
    runtime.check("After idle.")

    assert runtime.process.pid == pid
    assert len(processes) == 1
    runtime.stop()


def test_dead_process_and_stale_state_restart_atomically(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    monkeypatch.setattr(LanguageToolClient, "check", _successful_check)
    runtime = make()
    runtime.start()
    first = processes[0]
    first.returncode = 17

    payload, _ = runtime.check("Recover this.")

    assert payload == {"matches": []}
    assert len(processes) == 2
    assert runtime.process is processes[1]
    assert runtime.base_url is not None
    runtime.stop()


def test_stale_port_and_request_timeout_restart_once(
    monkeypatch, runtime_factory
):
    make, processes, _ = runtime_factory
    user_attempts = 0

    def check(_self, text, *, timeout_seconds=None):
        nonlocal user_attempts
        if text != READINESS_PROBE_TEXT:
            user_attempts += 1
            if user_attempts == 1:
                raise LanguageToolRuntimeError(
                    "stale port", code="request_timeout", duration_ms=5000
                )
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()
    runtime.start()
    first_port = runtime._port

    payload, _ = runtime.check("Retry without losing this text.")

    assert payload == {"matches": []}
    assert user_attempts == 2
    assert len(processes) == 2
    assert processes[0].terminate_count == 1
    assert runtime._port != first_port
    runtime.stop()


def test_recovery_has_no_infinite_retry(monkeypatch, runtime_factory):
    make, processes, _ = runtime_factory
    user_attempts = 0

    def check(_self, text, *, timeout_seconds=None):
        nonlocal user_attempts
        if text != READINESS_PROBE_TEXT:
            user_attempts += 1
            raise LanguageToolRuntimeError(
                "timed out", code="request_timeout", duration_ms=5000
            )
        return {"matches": []}, 1.0

    monkeypatch.setattr(LanguageToolClient, "check", check)
    runtime = make()

    with pytest.raises(LanguageToolRuntimeError) as caught:
        runtime.check("Never discard this.")

    assert caught.value.code == "request_timeout"
    assert user_attempts == 2
    assert len(processes) == 2
    assert runtime.process is None


def test_shutdown_cleans_only_owned_process_and_uses_nonblocking_io(
    monkeypatch, runtime_factory
):
    make, processes, popen_kwargs = runtime_factory
    monkeypatch.setattr(LanguageToolClient, "check", _successful_check)
    runtime = make()
    runtime.start()
    owned = processes[0]
    unrelated = FakeProcess(9999)

    runtime.stop()

    assert owned.terminate_count == 1
    assert unrelated.terminate_count == 0
    assert runtime.process is None
    assert popen_kwargs[0]["stdout"] is subprocess.DEVNULL
    assert popen_kwargs[0]["stderr"] is subprocess.DEVNULL
