from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from offline_writing_reviser.logging_config import configure_logging
from offline_writing_reviser.settings import SettingsStore
from offline_writing_reviser.windows.controller import (
    build_production_services,
)


ACCEPTANCE_ENVIRONMENT = "OFFLINE_WRITING_REVISER_PRODUCTION_ACCEPTANCE"


def run_production_acceptance(
    request_path: Path,
    response_path: Path,
) -> int:
    """Exercise installed production services without desktop focus automation."""
    if os.environ.get(ACCEPTANCE_ENVIRONMENT) != "1":
        return 2

    response: dict[str, Any] = {"status": "error", "results": []}
    language_tool = None
    exit_code = 1
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        cases = _validate_request(request)
        config = SettingsStore().load()
        configure_logging(config.log_file)
        logger = logging.getLogger("offline-writing-reviser")
        proofread, paraphrase, language_tool = build_production_services(
            config, logger=logger
        )
        results = []
        for case in cases:
            service = proofread if case["mode"] == "proofread" else paraphrase
            revision = service.revise(case["input"])
            results.append(
                {
                    "id": case["id"],
                    "mode": case["mode"],
                    "input": case["input"],
                    "output": revision.revised_text,
                    "provider": revision.provider,
                    "model": revision.model,
                    "metadata": revision.metadata,
                }
            )
        process = language_tool.process
        response = {
            "status": "ok",
            "results": results,
            "language_tool": {
                **language_tool.dependency_status(),
                "process_id": process.pid if process is not None else None,
            },
        }
        exit_code = 0
    except Exception as exc:
        response["error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": "".join(
                traceback.format_exception(
                    exc.__class__, exc, exc.__traceback__
                )
            ),
        }
    finally:
        if language_tool is not None:
            language_tool.stop()
            response.setdefault("language_tool", {})["running_after_stop"] = (
                language_tool.is_running
            )
        _write_response(response_path, response)
    return exit_code


def _validate_request(request: Any) -> list[dict[str, str]]:
    if not isinstance(request, dict) or not isinstance(
        request.get("cases"), list
    ):
        raise ValueError("Acceptance request must contain a cases list")
    cases: list[dict[str, str]] = []
    for index, case in enumerate(request["cases"]):
        if not isinstance(case, dict):
            raise ValueError(f"Acceptance case {index} must be an object")
        identifier = case.get("id")
        mode = case.get("mode")
        text = case.get("input")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"Acceptance case {index} requires an id")
        if mode not in {"proofread", "paraphrase"}:
            raise ValueError(f"Acceptance case {identifier} has invalid mode")
        if not isinstance(text, str) or not text:
            raise ValueError(f"Acceptance case {identifier} requires input")
        cases.append({"id": identifier, "mode": mode, "input": text})
    if not cases:
        raise ValueError("Acceptance request must not be empty")
    return cases


def _write_response(path: Path, response: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(response, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)
