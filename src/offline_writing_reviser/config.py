from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from offline_writing_reviser.paths import app_data_dir

APP_DATA_DIR = app_data_dir()
DEFAULT_LOG_FILE = APP_DATA_DIR / "logs" / "writing-reviser.log"
DEFAULT_SETTINGS_FILE = APP_DATA_DIR / "settings.json"


@dataclass(frozen=True)
class OfflineWritingConfig:
    enabled: bool = True
    provider: str = "ollama_cli"
    model: str = "gemma3:4b"
    hotkey: str = "Ctrl+Alt+W"
    timeout_seconds: float = 45.0
    max_characters: int = 20_000
    chunk_characters: int = 2000
    ollama_executable: str = "ollama"
    log_file: Path = DEFAULT_LOG_FILE


def load_config_from_env() -> OfflineWritingConfig:
    return OfflineWritingConfig(
        model=os.environ.get("OWR_MODEL", OfflineWritingConfig.model),
        hotkey=os.environ.get("OWR_HOTKEY", OfflineWritingConfig.hotkey),
        timeout_seconds=float(
            os.environ.get("OWR_TIMEOUT_SECONDS", OfflineWritingConfig.timeout_seconds)
        ),
        max_characters=int(
            os.environ.get("OWR_MAX_CHARACTERS", OfflineWritingConfig.max_characters)
        ),
        chunk_characters=int(
            os.environ.get(
                "OWR_CHUNK_CHARACTERS", OfflineWritingConfig.chunk_characters
            )
        ),
        ollama_executable=os.environ.get(
            "OWR_OLLAMA_EXECUTABLE", OfflineWritingConfig.ollama_executable
        ),
        log_file=Path(os.environ.get("OWR_LOG_FILE", str(DEFAULT_LOG_FILE))),
    )
