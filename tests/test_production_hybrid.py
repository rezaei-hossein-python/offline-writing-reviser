from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.errors import (
    OfflineWritingLanguageToolUnavailable,
)
from offline_writing_reviser.core.hybrid_service import (
    HybridProofreadingService,
)
from offline_writing_reviser.diagnostics import (
    collect_diagnostics,
    format_diagnostics,
)
from offline_writing_reviser.proofreading.languagetool import (
    LanguageToolClient,
    LanguageToolRuntime,
    default_java_path,
    default_server_jar_path,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
    OllamaInferenceResult,
)
from offline_writing_reviser.provisioning import ModelProvisioner
from offline_writing_reviser.windows.controller import OfflineWritingRuntime


def match(
    source: str,
    value: str,
    replacements: list[str],
    rule_id: str,
    category: str = "GRAMMAR",
) -> dict:
    offset = source.index(value)
    return {
        "offset": offset,
        "length": len(value),
        "message": "Objective proofreading evidence",
        "shortMessage": "Proofreading evidence",
        "replacements": [{"value": item} for item in replacements],
        "rule": {
            "id": rule_id,
            "description": "Test rule",
            "issueType": "grammar",
            "category": {"id": category, "name": category.title()},
        },
    }


class FakeLanguageTool:
    def __init__(self, payloads: list[dict] | None = None, error=None):
        self.payloads = list(payloads or [])
        self.error = error
        self.calls: list[str] = []

    def check(self, text: str):
        self.calls.append(text)
        if self.error:
            raise self.error
        return (
            self.payloads.pop(0)
            if self.payloads
            else {"matches": []}
        ), 0.01


class FakeProvider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, output: str = "", error=None):
        self.output = output
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def revise_with_telemetry(self, text, instruction, timeout_seconds):
        self.calls.append((text, instruction))
        if self.error:
            raise self.error
        return OllamaInferenceResult(
            self.output,
            {
                "load_duration_seconds": 1.0,
                "prompt_eval_duration_seconds": 2.0,
                "generation_duration_seconds": 0.5,
                "prompt_token_count": 100,
                "generation_token_count": 5,
            },
        )

    def runtime_diagnostics(self, timeout_seconds=2.0):
        return {"acceleration": "cpu"}


def service(lt, provider=None):
    return HybridProofreadingService(
        provider=provider or FakeProvider(),
        language_tool=lt,
        config=OfflineWritingConfig(chunk_characters=2000),
        logger=logging.getLogger("production-hybrid-test"),
    )


def test_clean_production_text_is_unchanged_and_bypasses_gemma():
    lt = FakeLanguageTool([{"matches": []}, {"matches": []}])
    provider = FakeProvider("must not be used")

    result = service(lt, provider).revise("The report is ready.")

    assert result.revised_text == "The report is ready."
    assert provider.calls == []
    assert result.metadata["gemma_routed"] == 0


def test_safe_languagetool_correction_is_applied_without_gemma():
    source = "Send it to the adress today."
    lt = FakeLanguageTool(
        [
            {
                "matches": [
                    match(
                        source,
                        "adress",
                        ["dress", "address"],
                        "MORFOLOGIK_RULE_EN_US",
                        "TYPOS",
                    )
                ]
            },
            {"matches": []},
        ]
    )
    provider = FakeProvider("must not be used")

    result = service(lt, provider).revise(source)

    assert result.revised_text == "Send it to the address today."
    assert provider.calls == []
    assert result.metadata["safe_correction_count"] == 1
    assert result.metadata["safe_rule_ids"] == ["MORFOLOGIK_RULE_EN_US"]


def test_ambiguous_production_case_routes_and_accepts_exact_gemma_edit():
    source = "She work in finance."
    grammar_match = match(source, "work", ["works", "worked"], "SHE_LIVE")
    provider = FakeProvider("She works in finance.")

    result = service(
        FakeLanguageTool(
            [{"matches": [grammar_match]}, {"matches": [grammar_match]}]
        ),
        provider,
    ).revise(source)

    assert result.revised_text == "She works in finance."
    assert len(provider.calls) == 1
    assert result.metadata["gemma_routed"] == 1
    assert result.metadata["gemma_accepted"] == 1


def test_validator_rejection_falls_back_to_safe_text():
    source = "She work in finance."
    grammar_match = match(source, "work", ["works", "worked"], "SHE_LIVE")

    result = service(
        FakeLanguageTool(
            [{"matches": [grammar_match]}, {"matches": [grammar_match]}]
        ),
        FakeProvider("Here is the corrected text: She works in finance."),
    ).revise(source)

    assert result.revised_text == source
    assert result.metadata["gemma_fallback"] == 1


def test_candidate_with_remaining_language_tool_error_is_rejected():
    source = "This criteria is mandatory."
    original_match = match(
        source,
        "This criteria",
        ["This criterion", "These criteria"],
        "THIS_NNS",
    )
    bad_output = "These criteria is mandatory."
    remaining_match = match(
        bad_output,
        "These criteria is",
        ["These criteria are"],
        "PERS_PRONOUN_AGREEMENT",
    )

    result = service(
        FakeLanguageTool(
            [
                {"matches": [original_match]},
                {"matches": [original_match]},
                {"matches": [remaining_match]},
            ]
        ),
        FakeProvider(bad_output),
    ).revise(source)

    assert result.revised_text == source
    assert result.metadata["gemma_accepted"] == 0
    assert result.metadata["gemma_fallback"] == 1


@pytest.mark.parametrize(
    "error",
    [
        OfflineWritingProviderUnavailable("Ollama unavailable"),
        OfflineWritingProviderTimeout("Inference timed out"),
    ],
)
def test_ollama_failure_falls_back_without_losing_safe_text(error):
    source = "She work in finance."
    grammar_match = match(source, "work", ["works", "worked"], "SHE_LIVE")
    result = service(
        FakeLanguageTool(
            [{"matches": [grammar_match]}, {"matches": [grammar_match]}]
        ),
        FakeProvider(error=error),
    ).revise(source)

    assert result.revised_text == source
    assert result.metadata["gemma_fallback"] == 1


def test_languagetool_unavailable_aborts_the_revision():
    lt = FakeLanguageTool(
        error=OfflineWritingLanguageToolUnavailable("missing runtime")
    )
    with pytest.raises(OfflineWritingLanguageToolUnavailable):
        service(lt).revise("Text.")


def test_production_formatting_and_newlines_are_preserved():
    source = "Items:\r\n- The adress\r\n\r\nEnd."
    lt = FakeLanguageTool(
        [
            {
                "matches": [
                    match(
                        source,
                        "adress",
                        ["address"],
                        "MORFOLOGIK_RULE_EN_US",
                        "TYPOS",
                    )
                ]
            },
            {"matches": []},
        ]
    )
    result = service(lt).revise(source)
    assert result.revised_text == "Items:\r\n- The address\r\n\r\nEnd."


@pytest.mark.parametrize(
    ("source", "original", "replacements", "rule_id", "expected"),
    [
        (
            "He go to work every day.",
            "go",
            ["goes", "went"],
            "HE_VERB_AGR",
            "He goes to work every day.",
        ),
        (
            "This sentense has a speling mistake.",
            "sentense",
            ["sentence", "sen tense"],
            "MORFOLOGIK_RULE_EN_US",
            "This sentence has a spelling mistake.",
        ),
    ],
)
def test_required_production_contextual_acceptance_cases(
    source, original, replacements, rule_id, expected
):
    first_match = match(source, original, replacements, rule_id, "TYPOS")
    result = service(
        FakeLanguageTool(
            [
                {"matches": [first_match]},
                {"matches": [first_match]},
            ]
        ),
        FakeProvider(expected),
    ).revise(source)

    assert result.revised_text == expected
    assert result.metadata["gemma_routed"] == 1
    assert result.metadata["gemma_accepted"] == 1


def test_required_production_mixed_spelling_and_grammar_case():
    source = "These equipement is expensive."
    safe_text = "These equipment is expensive."
    spelling = match(
        source,
        "equipement",
        ["equipment"],
        "MORFOLOGIK_RULE_EN_US",
        "TYPOS",
    )
    agreement = match(
        safe_text,
        "These equipment",
        ["This equipment", "These equipments"],
        "THIS_NNS",
    )

    result = service(
        FakeLanguageTool(
            [{"matches": [spelling]}, {"matches": [agreement]}]
        ),
        FakeProvider("This equipment is expensive."),
    ).revise(source)

    assert result.revised_text == "This equipment is expensive."
    assert result.metadata["safe_correction_count"] == 2
    assert result.metadata["gemma_routed"] == 0


def test_production_performance_logs_do_not_contain_user_text(caplog):
    source = "Confidential Alpha sentence."
    caplog.set_level(logging.INFO, logger="production-hybrid-test")
    service(FakeLanguageTool([{"matches": []}, {"matches": []}])).revise(
        source
    )
    assert source not in caplog.text
    assert "chars=" in caplog.text


def test_languagetool_runtime_retries_one_failed_request(monkeypatch):
    runtime = LanguageToolRuntime(
        Path("java.exe"), Path("languagetool-server.jar")
    )
    clients = [
        type(
            "Failed",
            (),
            {
                "check": lambda self, text: (_ for _ in ()).throw(
                    OfflineWritingLanguageToolUnavailable("crashed")
                )
            },
        )(),
        type(
            "Healthy",
            (),
            {"check": lambda self, text: ({"matches": []}, 0.01)},
        )(),
    ]
    monkeypatch.setattr(runtime, "client", lambda: clients.pop(0))

    payload, latency = runtime.check("Text.")

    assert payload == {"matches": []}
    assert latency == 0.01


def test_languagetool_runtime_cannot_restart_after_shutdown(monkeypatch):
    runtime = LanguageToolRuntime(
        Path("java.exe"), Path("languagetool-server.jar")
    )
    runtime.stop()
    monkeypatch.setattr(
        runtime,
        "_ensure_running",
        lambda: pytest.fail("shutdown runtime attempted to restart"),
    )

    with pytest.raises(
        OfflineWritingLanguageToolUnavailable, match="shutting down"
    ):
        runtime.check("Text.")


def test_languagetool_runtime_reports_missing_private_java(tmp_path):
    runtime = LanguageToolRuntime(
        tmp_path / "missing-java.exe", tmp_path / "server.jar"
    )
    with pytest.raises(
        OfflineWritingLanguageToolUnavailable, match="Java executable"
    ):
        runtime.client()


def test_languagetool_client_rejects_malformed_response(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"unexpected": []}).encode()

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: Response()
    )
    with pytest.raises(OfflineWritingLanguageToolUnavailable):
        LanguageToolClient("http://127.0.0.1:1").check("Text.")


def test_runtime_shutdown_stops_hotkey_controller_and_languagetool():
    calls: list[str] = []
    hotkey = type(
        "Hotkey", (), {"stop": lambda self: calls.append("hotkey")}
    )()
    controller = type(
        "Controller", (), {"stop": lambda self: calls.append("controller")}
    )()
    language_tool = type(
        "LanguageTool",
        (),
        {"stop": lambda self: calls.append("languagetool")},
    )()
    runtime = OfflineWritingRuntime(
        hotkey,
        controller=controller,
        language_tool=language_tool,
    )

    runtime.stop()

    assert calls == ["hotkey", "controller", "languagetool"]


def test_private_dependency_paths_resolve_under_vendor_in_source_tree():
    assert default_java_path().parts[-3:] == ("java", "bin", "javaw.exe")
    assert default_server_jar_path().parts[-2:] == (
        "languagetool",
        "languagetool-server.jar",
    )


def test_ollama_runtime_telemetry_reports_cpu_gpu_and_unknown(monkeypatch):
    provider = OllamaCliOfflineWritingProvider("gemma3:4b")

    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args: {
            "models": [
                {
                    "name": "gemma3:4b",
                    "size": 1000,
                    "size_vram": 0,
                    "context_length": 8192,
                }
            ]
        },
    )
    assert provider.runtime_diagnostics()["acceleration"] == "cpu"

    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *args: {
            "models": [
                {
                    "name": "gemma3:4b",
                    "size": 1000,
                    "size_vram": 1000,
                }
            ]
        },
    )
    assert provider.runtime_diagnostics()["acceleration"] == "gpu"

    monkeypatch.setattr(
        provider, "_request_json", lambda *args: {"models": []}
    )
    assert provider.runtime_diagnostics()["acceleration"] == "unknown"


def test_diagnostics_output_handles_unknown_backend(monkeypatch, tmp_path):
    class DiagnosticRuntime:
        java_path = tmp_path / "java.exe"
        is_running = True
        base_url = "http://127.0.0.1:10000"

        def dependency_status(self):
            return {
                "java_path": str(self.java_path),
                "java_found": True,
                "server_jar_path": "server.jar",
                "languagetool_found": True,
                "version": "6.6",
                "running": True,
                "base_url": self.base_url,
            }

        def check(self, text):
            return (
                {
                    "matches": [
                        match(
                            text,
                            "adress",
                            ["address"],
                            "MORFOLOGIK_RULE_EN_US",
                            "TYPOS",
                        )
                    ]
                },
                0.01,
            )

        def stop(self):
            pass

    class DiagnosticProvider:
        def resolved_executable(self):
            return "ollama.exe"

        def api_version(self, timeout_seconds=5.0):
            return "1.2.3"

        def cli_version(self, timeout_seconds=5.0):
            return "1.2.3"

        def api_models(self, timeout_seconds=5.0):
            return ["gemma3:4b"]

        def runtime_diagnostics(self, timeout_seconds=5.0):
            return {
                "model_loaded": False,
                "acceleration": "unknown",
                "model_vram_bytes": None,
                "context_length": None,
                "device": None,
                "backend": None,
            }

    monkeypatch.setattr(
        "offline_writing_reviser.diagnostics._java_version",
        lambda path: 'openjdk version "17"',
    )
    config = OfflineWritingConfig(
        model="gemma3:4b", log_file=tmp_path / "app.log"
    )
    report, healthy = collect_diagnostics(
        config,
        language_tool=DiagnosticRuntime(),
        provider=DiagnosticProvider(),
    )
    output = format_diagnostics(report)

    assert healthy is True
    assert "Acceleration: unknown" in output
    assert "Not exposed by Ollama" in output
    assert "adress" not in output


def test_diagnostics_always_stops_owned_languagetool(tmp_path):
    class BrokenRuntime:
        java_path = tmp_path / "java.exe"

        def __init__(self):
            self.stopped = False

        def dependency_status(self):
            raise RuntimeError("diagnostic setup failed")

        def stop(self):
            self.stopped = True

    runtime = BrokenRuntime()
    config = OfflineWritingConfig(
        model="gemma3:4b", log_file=tmp_path / "app.log"
    )

    with pytest.raises(RuntimeError, match="diagnostic setup failed"):
        collect_diagnostics(config, language_tool=runtime)

    assert runtime.stopped is True


def test_installer_and_build_scripts_are_relocatable_and_exclude_models():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "installer" / "OfflineWritingReviser.iss").read_text(
        encoding="utf-8"
    )
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert "D:\\Projects" not in installer + build
    assert "runtime\\java" in build
    assert "runtime\\languagetool" in build
    assert "gemma3:4b" not in installer
    assert "OllamaSetup.exe" in installer


def test_model_provisioner_streams_progress_and_verifies_install(monkeypatch):
    class Provider:
        def __init__(self):
            self.calls = 0

        def api_models(self, timeout_seconds=5.0):
            self.calls += 1
            return [] if self.calls == 1 else ["gemma3:4b"]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            yield b'{"status":"pulling","completed":5,"total":10}\n'
            yield b'{"status":"success"}\n'

    monkeypatch.setattr(
        "offline_writing_reviser.provisioning.urllib.request.urlopen",
        lambda *args, **kwargs: Response(),
    )
    updates = []
    provisioner = ModelProvisioner(
        OfflineWritingConfig(model="gemma3:4b"), provider=Provider()
    )

    assert provisioner.model_installed() is False
    provisioner.pull_model(
        lambda status, completed, total: updates.append(
            (status, completed, total)
        )
    )

    assert updates[0] == ("pulling", 5, 10)
