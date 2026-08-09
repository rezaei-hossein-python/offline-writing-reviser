from __future__ import annotations

import sys
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
