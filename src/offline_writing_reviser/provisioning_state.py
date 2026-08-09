from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from offline_writing_reviser.config import APP_DATA_DIR


class ProvisioningPhase(str, Enum):
    IDLE = "idle"
    CHECKING_OLLAMA = "checking_ollama"
    INSTALLING_OLLAMA = "installing_ollama"
    STARTING_OLLAMA = "starting_ollama"
    CHECKING_MODEL = "checking_model"
    DOWNLOADING_MODEL = "downloading_model"
    VERIFYING_MODEL = "verifying_model"
    TESTING_INFERENCE = "testing_inference"
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ProvisioningSnapshot:
    phase: ProvisioningPhase = ProvisioningPhase.IDLE
    current_stage: str = "Ready to set up intelligent revision."
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    percentage: int | None = None
    latest_error: str | None = None
    retry_available: bool = False
    active: bool = False
    ready: bool = False
    process_id: int | None = None
    removed_model: str | None = None
    recovered_bytes: int | None = None
    updated_at: float = 0.0


class ProvisioningStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or APP_DATA_DIR / "provisioning" / "state.json"

    def save(self, snapshot: ProvisioningSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        payload = asdict(snapshot)
        payload["phase"] = snapshot.phase.value
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def load(self) -> ProvisioningSnapshot:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return ProvisioningSnapshot()
            snapshot = _snapshot_from_payload(payload)
        except (OSError, ValueError, TypeError):
            return ProvisioningSnapshot()
        if snapshot.active and not _process_is_running(snapshot.process_id):
            return ProvisioningSnapshot(
                phase=ProvisioningPhase.FAILED,
                current_stage="AI setup was interrupted.",
                downloaded_bytes=snapshot.downloaded_bytes,
                total_bytes=snapshot.total_bytes,
                percentage=snapshot.percentage,
                latest_error=(
                    "The previous setup process stopped unexpectedly. "
                    "Choose Retry to resume."
                ),
                retry_available=True,
                updated_at=time.time(),
            )
        return snapshot

    def is_active(self) -> bool:
        return self.load().active


def _snapshot_from_payload(payload: dict[str, Any]) -> ProvisioningSnapshot:
    phase = ProvisioningPhase(str(payload.get("phase", "idle")))
    return ProvisioningSnapshot(
        phase=phase,
        current_stage=str(payload.get("current_stage", "")),
        downloaded_bytes=_optional_int(payload.get("downloaded_bytes")),
        total_bytes=_optional_int(payload.get("total_bytes")),
        percentage=_optional_int(payload.get("percentage")),
        latest_error=(
            str(payload["latest_error"])
            if payload.get("latest_error") is not None
            else None
        ),
        retry_available=bool(payload.get("retry_available", False)),
        active=bool(payload.get("active", False)),
        ready=bool(payload.get("ready", False)),
        process_id=_optional_int(payload.get("process_id")),
        removed_model=(
            str(payload["removed_model"])
            if payload.get("removed_model") is not None
            else None
        ),
        recovered_bytes=_optional_int(payload.get("recovered_bytes")),
        updated_at=float(payload.get("updated_at", 0.0)),
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _process_is_running(process_id: int | None) -> bool:
    if not process_id:
        return False
    if process_id == os.getpid():
        return True
    try:
        os.kill(process_id, 0)
    except (OSError, ValueError):
        return False
    return True
