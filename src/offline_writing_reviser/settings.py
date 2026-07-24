from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from offline_writing_reviser.config import (
    DEFAULT_LOG_FILE,
    DEFAULT_SETTINGS_FILE,
    OfflineWritingConfig,
)
from offline_writing_reviser.windows.hotkeys import parse_hotkey


SETTINGS_KEYS = ("model", "timeout_seconds", "max_characters", "hotkey")


class SettingsValidationError(ValueError):
    pass


def validate_config(config: OfflineWritingConfig) -> OfflineWritingConfig:
    model = config.model.strip()
    hotkey = config.hotkey.strip()
    if not model or len(model) > 200:
        raise SettingsValidationError("Select a valid installed Ollama model.")
    if not 5 <= float(config.timeout_seconds) <= 600:
        raise SettingsValidationError("Revision timeout must be between 5 and 600 seconds.")
    if not 100 <= int(config.max_characters) <= 100_000:
        raise SettingsValidationError("Maximum input length must be between 100 and 100,000.")
    try:
        modifiers, _key = parse_hotkey(hotkey)
    except ValueError as exc:
        raise SettingsValidationError(
            "Hotkey must use Ctrl and/or Alt plus one letter or number."
        ) from exc
    if not modifiers:
        raise SettingsValidationError(
            "Hotkey must use Ctrl and/or Alt plus one letter or number."
        )
    return replace(
        config,
        model=model,
        hotkey=canonicalize_hotkey(hotkey),
        timeout_seconds=float(config.timeout_seconds),
        max_characters=int(config.max_characters),
    )


def canonicalize_hotkey(shortcut: str) -> str:
    parts = [part.strip() for part in shortcut.split("+")]
    result: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered == "ctrl":
            result.append("Ctrl")
        elif lowered == "alt":
            result.append("Alt")
        else:
            result.append(part.upper())
    return "+".join(result)


class SettingsStore:
    def __init__(
        self,
        path: Path = DEFAULT_SETTINGS_FILE,
        defaults: OfflineWritingConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        self.path = path
        self.defaults = defaults or OfflineWritingConfig(
            log_file=DEFAULT_LOG_FILE,
        )
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self.recovered_corrupt_file = False

    def load(self) -> OfflineWritingConfig:
        self.recovered_corrupt_file = False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise SettingsValidationError("Settings root must be an object")
            values = {key: raw[key] for key in SETTINGS_KEYS if key in raw}
            config = validate_config(replace(self.defaults, **values))
        except FileNotFoundError:
            config = self.defaults
        except (OSError, json.JSONDecodeError, TypeError, SettingsValidationError, ValueError):
            self.recovered_corrupt_file = True
            self.logger.warning(
                "Settings file was invalid; defaults restored path=%s", self.path
            )
            self._preserve_corrupt_file()
            config = self.defaults
        return _apply_environment_overrides(config)

    def save(self, config: OfflineWritingConfig) -> OfflineWritingConfig:
        validated = validate_config(config)
        data = {key: _json_value(getattr(validated, key)) for key in SETTINGS_KEYS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self.logger.info(
            "Settings saved model=%s hotkey=%s timeout_seconds=%s max_characters=%s",
            validated.model,
            validated.hotkey,
            validated.timeout_seconds,
            validated.max_characters,
        )
        return validated

    def reset(self) -> OfflineWritingConfig:
        return self.save(self.defaults)

    def _preserve_corrupt_file(self) -> None:
        if not self.path.exists():
            return
        backup = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            os.replace(self.path, backup)
        except OSError:
            self.logger.exception("Could not preserve corrupt settings file")


def config_with_updates(
    config: OfflineWritingConfig,
    *,
    model: str,
    timeout_seconds: float,
    max_characters: int,
    hotkey: str,
) -> OfflineWritingConfig:
    return validate_config(
        replace(
            config,
            model=model,
            timeout_seconds=timeout_seconds,
            max_characters=max_characters,
            hotkey=hotkey,
        )
    )


def public_settings(config: OfflineWritingConfig) -> dict[str, Any]:
    values = asdict(config)
    return {key: values[key] for key in SETTINGS_KEYS}


def _json_value(value: Any) -> Any:
    return str(value) if isinstance(value, Path) else value


def _apply_environment_overrides(config: OfflineWritingConfig) -> OfflineWritingConfig:
    updates: dict[str, Any] = {}
    if "OWR_MODEL" in os.environ:
        updates["model"] = os.environ["OWR_MODEL"]
    if "OWR_HOTKEY" in os.environ:
        updates["hotkey"] = os.environ["OWR_HOTKEY"]
    if "OWR_TIMEOUT_SECONDS" in os.environ:
        updates["timeout_seconds"] = float(os.environ["OWR_TIMEOUT_SECONDS"])
    if "OWR_MAX_CHARACTERS" in os.environ:
        updates["max_characters"] = int(os.environ["OWR_MAX_CHARACTERS"])
    if "OWR_OLLAMA_EXECUTABLE" in os.environ:
        updates["ollama_executable"] = os.environ["OWR_OLLAMA_EXECUTABLE"]
    if "OWR_LOG_FILE" in os.environ:
        updates["log_file"] = Path(os.environ["OWR_LOG_FILE"])
    return validate_config(replace(config, **updates))
