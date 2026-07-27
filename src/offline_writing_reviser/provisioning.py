from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from offline_writing_reviser.config import (
    APP_DATA_DIR,
    OfflineWritingConfig,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderError,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.providers.ollama import (
    OLLAMA_API_URL,
    OllamaCliOfflineWritingProvider,
)


OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DOWNLOAD_CHUNK_SIZE = 1024 * 256
ProgressCallback = Callable[[str, int | None, int | None], None]
CancelCheck = Callable[[], bool]


class ProvisioningCancelled(OfflineWritingProviderError):
    pass


class ModelProvisioner:
    def __init__(
        self,
        config: OfflineWritingConfig,
        provider: OllamaCliOfflineWritingProvider | None = None,
    ):
        self.config = config
        self.provider = provider or OllamaCliOfflineWritingProvider(
            model=config.model, executable=config.ollama_executable
        )

    def model_installed(self) -> bool:
        return self.config.model in self.provider.api_models(
            timeout_seconds=5.0
        )

    def pull_model(
        self,
        progress: ProgressCallback,
        *,
        timeout_seconds: float = 30.0,
        cancelled: CancelCheck | None = None,
    ) -> None:
        payload = {
            "model": self.config.model,
            "stream": True,
            "insecure": False,
        }
        request = urllib.request.Request(
            f"{OLLAMA_API_URL}/api/pull",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                for raw_line in response:
                    if cancelled and cancelled():
                        raise ProvisioningCancelled(
                            "Model download cancelled. A later retry can resume "
                            "the Ollama layer download."
                        )
                    if not raw_line.strip():
                        continue
                    update = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(update, dict):
                        continue
                    error = update.get("error")
                    if isinstance(error, str) and error:
                        raise OfflineWritingProviderError(
                            f"Model download failed: {error}"
                        )
                    progress(
                        str(update.get("status", "Downloading model")),
                        _optional_int(update.get("completed")),
                        _optional_int(update.get("total")),
                    )
        except ProvisioningCancelled:
            raise
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            raise OfflineWritingProviderUnavailable(
                "The model download was interrupted. Check the connection and "
                "choose Retry; Ollama will reuse completed layers."
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OfflineWritingProviderError(
                "Ollama returned invalid model-download progress"
            ) from exc
        if not self.model_installed():
            raise OfflineWritingProviderError(
                "Ollama completed without installing the required model"
            )


class AIProvisioner:
    """Provision optional Ollama dependencies independently of core setup."""

    def __init__(
        self,
        config: OfflineWritingConfig,
        model_provisioner: ModelProvisioner | None = None,
        cache_directory: Path | None = None,
    ):
        self.config = config
        self.model = model_provisioner or ModelProvisioner(config)
        self.cache_directory = (
            cache_directory or APP_DATA_DIR / "provisioning"
        )

    def provision(
        self,
        progress: ProgressCallback,
        *,
        cancelled: CancelCheck | None = None,
        install_stage: Callable[[bool], None] | None = None,
    ) -> None:
        cancelled = cancelled or (lambda: False)
        progress("Checking for Ollama", None, None)
        try:
            self.model.provider.resolved_executable()
        except OfflineWritingProviderError:
            installer = self.download_ollama(progress, cancelled=cancelled)
            if install_stage:
                install_stage(True)
            try:
                self.install_ollama(installer)
            finally:
                if install_stage:
                    install_stage(False)

        if cancelled():
            raise ProvisioningCancelled("AI setup was cancelled.")
        progress("Starting Ollama", None, None)
        self.model.provider.ensure_api_running(timeout_seconds=30.0)
        if self.model.model_installed():
            progress(f"{self.config.model} is already installed", 1, 1)
            return
        progress(f"Preparing {self.config.model}", None, None)
        self.model.pull_model(
            progress,
            timeout_seconds=30.0,
            cancelled=cancelled,
        )
        progress("AI proofreader is ready", 1, 1)

    def download_ollama(
        self,
        progress: ProgressCallback,
        *,
        cancelled: CancelCheck,
        timeout_seconds: float = 30.0,
    ) -> Path:
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        destination = self.cache_directory / "OllamaSetup.exe"
        partial = destination.with_suffix(".exe.part")
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        request = urllib.request.Request(
            OLLAMA_INSTALLER_URL, headers=headers
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                status = getattr(response, "status", 200)
                append = existing > 0 and status == 206
                downloaded = existing if append else 0
                length = _header_int(response, "Content-Length")
                total = (
                    downloaded + length
                    if length is not None
                    else None
                )
                mode = "ab" if append else "wb"
                with partial.open(mode) as target:
                    while True:
                        if cancelled():
                            raise ProvisioningCancelled(
                                "Ollama download cancelled. The partial download "
                                "was kept for Retry."
                            )
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        target.write(chunk)
                        downloaded += len(chunk)
                        progress(
                            "Downloading Ollama",
                            downloaded,
                            total,
                        )
        except ProvisioningCancelled:
            raise
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            raise OfflineWritingProviderUnavailable(
                "The Ollama installer download was interrupted. Check the "
                "connection and choose Retry to resume it."
            ) from exc
        partial.replace(destination)
        return destination

    def install_ollama(self, installer: Path) -> None:
        try:
            process = subprocess.Popen([str(installer)])
            while process.poll() is None:
                time.sleep(0.25)
        except OSError as exc:
            raise OfflineWritingProviderUnavailable(
                "The official Ollama installer could not be started."
            ) from exc
        if process.returncode != 0:
            raise OfflineWritingProviderError(
                f"Ollama installer exited with code {process.returncode}."
            )
        try:
            self.model.provider.resolved_executable()
        except OfflineWritingProviderError as exc:
            raise OfflineWritingProviderUnavailable(
                "Ollama installation completed but ollama.exe was not found."
            ) from exc


def run_model_provisioning(config: OfflineWritingConfig | None = None) -> int:
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
    )

    config = config or OfflineWritingConfig()
    app = QApplication.instance() or QApplication([])
    consent = QMessageBox.question(
        None,
        "Set up optional AI proofreading",
        "LanguageTool proofreading is already available without AI.\n\n"
        f"AI setup reuses a compatible Ollama installation when present and "
        f"otherwise downloads the official Ollama installer. It then downloads "
        f"{config.model} (approximately 3 GB, subject to the Gemma Terms of "
        "Use). Core application installation does not depend on this step.\n\n"
        "Continue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if consent != QMessageBox.StandardButton.Yes:
        return 0

    provisioner = AIProvisioner(config)
    dialog = QDialog()
    dialog.setWindowTitle("Offline Writing Reviser — AI Setup")
    dialog.setAccessibleName("AI proofreading setup")
    dialog.setMinimumWidth(500)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    layout = QVBoxLayout(dialog)
    label = QLabel("Ready to set up AI proofreading.")
    label.setAccessibleName("AI setup current stage")
    detail = QLabel("")
    detail.setAccessibleName("AI setup download details")
    detail.setWordWrap(True)
    progress_bar = QProgressBar()
    progress_bar.setAccessibleName("AI setup progress")
    progress_bar.setRange(0, 0)
    button_layout = QHBoxLayout()
    retry_button = QPushButton("Retry")
    retry_button.setAccessibleName("Retry AI setup")
    cancel_button = QPushButton("Cancel")
    cancel_button.setAccessibleName("Cancel AI setup")
    close_button = QPushButton("Close")
    close_button.setAccessibleName("Close AI setup")
    retry_button.setEnabled(False)
    close_button.setEnabled(False)
    button_layout.addStretch()
    button_layout.addWidget(retry_button)
    button_layout.addWidget(cancel_button)
    button_layout.addWidget(close_button)
    layout.addWidget(label)
    layout.addWidget(detail)
    layout.addWidget(progress_bar)
    layout.addLayout(button_layout)

    outcome = {"code": 1, "working": False}
    cancel_event = threading.Event()
    active: dict[str, object] = {}

    class Worker(QObject):
        progress = Signal(str, int, int)
        install_stage = Signal(bool)
        succeeded = Signal()
        failed = Signal(str, bool)

        def run(self) -> None:
            try:
                provisioner.provision(
                    lambda status, completed, total: self.progress.emit(
                        status, completed or 0, total or 0
                    ),
                    cancelled=cancel_event.is_set,
                    install_stage=self.install_stage.emit,
                )
            except ProvisioningCancelled as exc:
                self.failed.emit(str(exc), True)
            except Exception as exc:
                self.failed.emit(
                    f"{exc.__class__.__name__}: {exc}", False
                )
            else:
                self.succeeded.emit()

    def update(status: str, completed: int, total: int) -> None:
        label.setText(status)
        if total > 0:
            progress_bar.setRange(0, 1000)
            progress_bar.setValue(min(1000, int(completed * 1000 / total)))
            detail.setText(
                f"{_format_bytes(completed)} of {_format_bytes(total)}"
            )
        else:
            progress_bar.setRange(0, 0)
            detail.setText("")

    def set_install_stage(active_stage: bool) -> None:
        cancel_button.setEnabled(not active_stage)
        if active_stage:
            label.setText("Installing Ollama")
            detail.setText(
                "Complete the official Ollama installer. Cancellation is "
                "disabled during this external installation step."
            )

    def finish_thread() -> None:
        outcome["working"] = False
        thread = active.get("thread")
        if isinstance(thread, QThread):
            thread.quit()

    def succeeded() -> None:
        finish_thread()
        outcome["code"] = 0
        label.setText("AI proofreading is ready.")
        detail.setText("")
        progress_bar.setRange(0, 100)
        progress_bar.setValue(100)
        retry_button.setEnabled(False)
        cancel_button.setEnabled(False)
        close_button.setEnabled(True)
        close_button.setFocus()

    def failed(message: str, was_cancelled: bool) -> None:
        finish_thread()
        outcome["code"] = 1
        label.setText("AI setup cancelled." if was_cancelled else "AI setup failed.")
        detail.setText(message)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        retry_button.setEnabled(True)
        cancel_button.setEnabled(False)
        close_button.setEnabled(True)
        retry_button.setFocus()

    def start_attempt() -> None:
        if outcome["working"]:
            return
        cancel_event.clear()
        outcome["working"] = True
        retry_button.setEnabled(False)
        close_button.setEnabled(False)
        cancel_button.setEnabled(True)
        label.setText("Checking AI components…")
        detail.setText("")
        progress_bar.setRange(0, 0)
        thread = QThread(dialog)
        worker = Worker()
        worker.moveToThread(thread)
        worker.progress.connect(update)
        worker.install_stage.connect(set_install_stage)
        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        thread.started.connect(worker.run)
        active["thread"] = thread
        active["worker"] = worker
        thread.start()

    def cancel() -> None:
        if outcome["working"]:
            cancel_event.set()
            label.setText("Cancelling safely…")
            cancel_button.setEnabled(False)
        else:
            dialog.reject()

    retry_button.clicked.connect(start_attempt)
    cancel_button.clicked.connect(cancel)
    close_button.clicked.connect(dialog.accept)
    dialog.rejected.connect(
        lambda: cancel_event.set() if outcome["working"] else None
    )
    start_attempt()
    dialog.exec()
    thread = active.get("thread")
    if isinstance(thread, QThread) and thread.isRunning():
        cancel_event.set()
        thread.quit()
        thread.wait(5000)
    return outcome["code"]


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _header_int(response: Any, name: str) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get(name) if headers is not None else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"
