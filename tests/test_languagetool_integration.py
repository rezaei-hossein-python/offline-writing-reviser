from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from offline_writing_reviser.correction.languagetool import (
    LanguageToolCorrectionService,
    LanguageToolRuntime,
)


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "vendor" / "java" / "bin" / "javaw.exe"
SERVER = ROOT / "vendor" / "languagetool" / "languagetool-server.jar"
RUNTIME_AVAILABLE = JAVA.is_file() and SERVER.is_file() and sys.platform == "win32"


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="private Windows runtime not prepared")
def test_real_languagetool_required_examples_latency_and_cleanup():
    runtime = LanguageToolRuntime(JAVA, SERVER)
    service = LanguageToolCorrectionService(runtime)
    try:
        spelling = service.correct("I recieved the adress yesterday.")
        grammar = service.correct("He go to work every day.")
        esl = service.correct("We discussed about the project.")
        correct = service.correct("The meeting starts at nine tomorrow morning.")
        paragraph = service.correct(
            "I recieved the adress yesterday. He go to work every day. "
            "We discussed about the project. The meeting starts at nine "
            "tomorrow morning."
        )
        pid = runtime.process.pid if runtime.process else None

        assert spelling.corrected_text == "I received the address yesterday."
        assert grammar.corrected_text == "He goes to work every day."
        assert esl.corrected_text == "We discussed the project."
        assert correct.corrected_text == correct.original_text
        assert paragraph.duration_ms < 1000
        assert max(grammar.duration_ms, esl.duration_ms, correct.duration_ms) < 500
        assert pid is not None
    finally:
        shutdown_ms = runtime.stop()

    assert shutdown_ms < 5000
    assert runtime.process is None


@pytest.mark.skipif(not RUNTIME_AVAILABLE, reason="private Windows runtime not prepared")
def test_real_languagetool_lifecycle_stress_restart_and_reuse():
    warm_latencies = []
    for _cycle in range(3):
        runtime = LanguageToolRuntime(JAVA, SERVER)
        service = LanguageToolCorrectionService(runtime)
        try:
            runtime.start_in_background()
            first_result = []
            hotkey = threading.Thread(
                target=lambda: first_result.append(
                    service.correct("I recieved the adress yesterday.")
                )
            )
            hotkey.start()
            hotkey.join(timeout=40)
            assert not hotkey.is_alive()
            assert first_result[0].corrected_text == (
                "I received the address yesterday."
            )
            first_process = runtime.process
            assert first_process is not None

            grammar = service.correct("He go to work every day.")
            repeated = service.correct("I recieved the adress yesterday.")
            time.sleep(0.25)
            after_idle = service.correct("He go to work every day.")
            warm_latencies.extend(
                [grammar.duration_ms, repeated.duration_ms, after_idle.duration_ms]
            )
            assert grammar.corrected_text == "He goes to work every day."
            assert repeated.corrected_text == (
                "I received the address yesterday."
            )
            assert after_idle.corrected_text == "He goes to work every day."
            assert runtime.process is first_process

            first_process.terminate()
            first_process.wait(timeout=5)
            recovered = service.correct("I recieved the adress yesterday.")
            assert recovered.corrected_text == (
                "I received the address yesterday."
            )
            assert runtime.process is not None
            assert runtime.process is not first_process
            assert runtime.process.poll() is None
        finally:
            runtime.stop()
        assert runtime.process is None

    assert max(warm_latencies) < 500
