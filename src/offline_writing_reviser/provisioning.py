from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderError,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.providers.ollama import (
    OLLAMA_API_URL,
    OllamaCliOfflineWritingProvider,
)


ProgressCallback = Callable[[str, int | None, int | None], None]


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
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            raise OfflineWritingProviderUnavailable(
                "Ollama model download is unavailable"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OfflineWritingProviderError(
                "Ollama returned invalid model-download progress"
            ) from exc
        if not self.model_installed():
            raise OfflineWritingProviderError(
                "Ollama completed without installing the required model"
            )


def run_model_provisioning(config: OfflineWritingConfig | None = None) -> int:
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QLabel,
        QMessageBox,
        QProgressBar,
        QVBoxLayout,
    )

    config = config or OfflineWritingConfig()
    app = QApplication.instance() or QApplication([])
    provisioner = ModelProvisioner(config)
    try:
        provisioner.provider.ensure_api_running(timeout_seconds=30.0)
        installed = provisioner.model_installed()
    except OfflineWritingProviderError as exc:
        QMessageBox.critical(
            None,
            "Ollama unavailable",
            "Ollama must be installed and running before the model can be "
            f"downloaded.\n\n{exc}",
        )
        return 1

    if not installed:
        consent = QMessageBox.question(
            None,
            "Download proofreading model",
            f"Offline Writing Reviser needs to download {config.model}. "
            "The download is approximately 3 GB and is subject to the Gemma "
            "Terms of Use. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if consent != QMessageBox.StandardButton.Yes:
            return 1

    class Worker(QObject):
        progress = Signal(str, int, int)
        succeeded = Signal()
        failed = Signal(str)

        def run(self) -> None:
            try:
                if not installed:
                    provisioner.pull_model(
                        lambda status, completed, total: self.progress.emit(
                            status, completed or 0, total or 0
                        )
                    )
                self.progress.emit(
                    "Validating local proofreading dependencies…", 0, 0
                )
                from offline_writing_reviser.diagnostics import (
                    collect_diagnostics,
                )

                _report, healthy = collect_diagnostics(
                    config,
                    include_gemma_test=True,
                    provider=provisioner.provider,
                )
                if not healthy:
                    raise OfflineWritingProviderError(
                        "Installed dependencies failed the proofreading health test. "
                        "Run --diagnostics for details."
                    )
            except Exception as exc:
                self.failed.emit(f"{exc.__class__.__name__}: {exc}")
                return
            self.succeeded.emit()

    dialog = QDialog()
    dialog.setWindowTitle("Offline Writing Reviser Setup")
    dialog.setAccessibleName("Proofreading model download")
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    layout = QVBoxLayout(dialog)
    label = QLabel(f"Preparing {config.model} download…")
    label.setAccessibleName("Model download status")
    progress_bar = QProgressBar()
    progress_bar.setAccessibleName("Model download progress")
    progress_bar.setRange(0, 0)
    layout.addWidget(label)
    layout.addWidget(progress_bar)
    thread = QThread(dialog)
    worker = Worker()
    worker.moveToThread(thread)
    outcome = {"code": 1}

    def update(status: str, completed: int, total: int) -> None:
        label.setText(status)
        if total > 0:
            progress_bar.setRange(0, 1000)
            progress_bar.setValue(min(1000, int(completed * 1000 / total)))
        else:
            progress_bar.setRange(0, 0)

    def succeeded() -> None:
        outcome["code"] = 0
        thread.quit()
        dialog.accept()

    def failed(message: str) -> None:
        thread.quit()
        QMessageBox.critical(
            dialog, "Model download failed", message
        )
        dialog.reject()

    worker.progress.connect(update)
    worker.succeeded.connect(succeeded)
    worker.failed.connect(failed)
    thread.started.connect(worker.run)
    thread.start()
    dialog.exec()
    if thread.isRunning():
        thread.quit()
        thread.wait(3000)
    return outcome["code"]


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
