from __future__ import annotations

import json
import logging
import os
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
from offline_writing_reviser.provisioning_state import (
    ProvisioningPhase,
    ProvisioningSnapshot,
    ProvisioningStateStore,
)


OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DOWNLOAD_CHUNK_SIZE = 1024 * 256
PROVISIONING_MUTEX_NAME = r"Local\OfflineWritingReviserProvisioningV1"
PROVISIONING_CONTROL_WINDOW_CLASS = "OfflineWritingReviserProvisioningControl"
PROVISIONING_CONTROL_WINDOW_TITLE = "Offline Writing Reviser Provisioning Control"
WM_PROVISIONING_SHOW = 0x8000 + 201
ProgressCallback = Callable[[str, int | None, int | None], None]
CancelCheck = Callable[[], bool]
StateCallback = Callable[[ProvisioningSnapshot], None]


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

    def verify_inference(self, timeout_seconds: float = 120.0) -> None:
        self.provider.verify_minimal_inference(timeout_seconds=timeout_seconds)

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
        logger = logging.getLogger("offline-writing-reviser")
        progress("Checking for Ollama", None, None)
        logger.info("Model provisioning state=checking_ollama")
        try:
            self.model.provider.resolved_executable()
        except OfflineWritingProviderError:
            logger.info("Model provisioning state=ollama_missing")
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
        progress("Verifying Ollama API", None, None)
        logger.info("Model provisioning state=checking_api")
        self.model.provider.ensure_api_running(timeout_seconds=30.0)
        logger.info("Model provisioning state=api_ready")
        if not self.model.model_installed():
            logger.warning(
                "Model provisioning state=model_missing model=%s",
                self.config.model,
            )
            progress(f"Preparing {self.config.model}", None, None)
            try:
                self.model.pull_model(
                    progress,
                    timeout_seconds=30.0,
                    cancelled=cancelled,
                )
            except ProvisioningCancelled:
                logger.warning(
                    "Model provisioning state=cancelled model=%s",
                    self.config.model,
                )
                raise
            except OfflineWritingProviderError:
                logger.exception(
                    "Model provisioning state=failed_pull model=%s",
                    self.config.model,
                )
                raise
        else:
            progress(f"{self.config.model} is already installed", 1, 1)
            logger.info(
                "Model provisioning state=model_installed model=%s reused=true",
                self.config.model,
            )
        if cancelled():
            raise ProvisioningCancelled("AI setup was cancelled.")
        progress("Verifying installed model", None, None)
        if not self.model.model_installed():
            raise OfflineWritingProviderError(
                "The required AI model is not installed after setup"
            )
        logger.info(
            "Model provisioning state=model_installed model=%s",
            self.config.model,
        )
        progress("Testing minimal inference", None, None)
        logger.info("Model provisioning state=testing_inference")
        self.model.verify_inference(timeout_seconds=120.0)
        logger.info(
            "Model provisioning state=ready model=%s", self.config.model
        )
        progress("Intelligent revision is ready", 1, 1)

    def download_ollama(
        self,
        progress: ProgressCallback,
        *,
        cancelled: CancelCheck,
        timeout_seconds: float = 30.0,
        _allow_range_restart: bool = True,
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
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing and _allow_range_restart:
                partial.unlink(missing_ok=True)
                return self.download_ollama(
                    progress,
                    cancelled=cancelled,
                    timeout_seconds=timeout_seconds,
                    _allow_range_restart=False,
                )
            raise OfflineWritingProviderUnavailable(
                "The Ollama installer download failed. Check the connection "
                "and choose Retry."
            ) from exc
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


class ProvisioningController:
    """Own one provisioning job and its state independently of any window."""

    def __init__(
        self,
        config: OfflineWritingConfig,
        *,
        provisioner: AIProvisioner | None = None,
        state_store: ProvisioningStateStore | None = None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.provisioner = provisioner or AIProvisioner(config)
        self.state_store = state_store or ProvisioningStateStore()
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._lock = threading.RLock()
        self._callbacks: list[StateCallback] = []
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = self.state_store.load()

    @property
    def snapshot(self) -> ProvisioningSnapshot:
        with self._lock:
            return self._snapshot

    def subscribe(self, callback: StateCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)
            snapshot = self._snapshot
        callback(snapshot)

    def start(self) -> bool:
        with self._lock:
            if self._snapshot.active or (
                self._thread is not None and self._thread.is_alive()
            ):
                return False
            self._cancel_event.clear()
            self._set_state_locked(
                ProvisioningSnapshot(
                    phase=ProvisioningPhase.CHECKING_OLLAMA,
                    current_stage="Checking for Ollama",
                    active=True,
                    process_id=os.getpid(),
                    updated_at=time.time(),
                )
            )
            thread = threading.Thread(
                target=self._run,
                name="offline-writing-model-provisioning",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        return True

    def cancel(self) -> bool:
        with self._lock:
            if not self._snapshot.active:
                return False
            self._cancel_event.set()
            self._publish_locked(
                current_stage="Cancelling safely...",
            )
        return True

    def wait(self, timeout_seconds: float = 5.0) -> bool:
        thread = self._thread
        if thread:
            thread.join(timeout=timeout_seconds)
        return not (thread and thread.is_alive())

    def _run(self) -> None:
        try:
            self.provisioner.provision(
                self._progress,
                cancelled=self._cancel_event.is_set,
                install_stage=self._install_stage,
            )
        except ProvisioningCancelled as exc:
            self.logger.warning("Model provisioning controller state=cancelled")
            self._finish(
                phase=ProvisioningPhase.CANCELLED,
                stage="AI setup cancelled.",
                error=str(exc),
                retry_available=True,
            )
        except Exception as exc:
            self.logger.exception(
                "Model provisioning controller state=failed category=%s",
                exc.__class__.__name__,
            )
            message = (
                str(exc)
                if isinstance(exc, OfflineWritingProviderError)
                else "Setup encountered an unexpected problem. Choose Retry."
            )
            self._finish(
                phase=ProvisioningPhase.FAILED,
                stage="AI setup failed.",
                error=message,
                retry_available=True,
            )
        else:
            self._finish(
                phase=ProvisioningPhase.READY,
                stage="Intelligent revision is ready.",
                ready=True,
            )

    def _progress(
        self,
        stage: str,
        downloaded: int | None,
        total: int | None,
    ) -> None:
        phase = _phase_for_progress(stage, downloaded, total)
        percentage = (
            min(100, max(0, int((downloaded or 0) * 100 / total)))
            if total
            else None
        )
        with self._lock:
            self._publish_locked(
                phase=phase,
                current_stage=stage,
                downloaded_bytes=downloaded,
                total_bytes=total,
                percentage=percentage,
            )

    def _install_stage(self, active: bool) -> None:
        with self._lock:
            self._publish_locked(
                phase=(
                    ProvisioningPhase.INSTALLING_OLLAMA
                    if active
                    else ProvisioningPhase.STARTING_OLLAMA
                ),
                current_stage=(
                    "Installing Ollama"
                    if active
                    else "Starting Ollama"
                ),
            )

    def _finish(
        self,
        *,
        phase: ProvisioningPhase,
        stage: str,
        error: str | None = None,
        retry_available: bool = False,
        ready: bool = False,
    ) -> None:
        with self._lock:
            self._publish_locked(
                phase=phase,
                current_stage=stage,
                latest_error=error,
                retry_available=retry_available,
                active=False,
                ready=ready,
                process_id=os.getpid(),
            )

    def _publish_locked(self, **updates: Any) -> None:
        values = {
            "phase": self._snapshot.phase,
            "current_stage": self._snapshot.current_stage,
            "downloaded_bytes": self._snapshot.downloaded_bytes,
            "total_bytes": self._snapshot.total_bytes,
            "percentage": self._snapshot.percentage,
            "latest_error": self._snapshot.latest_error,
            "retry_available": self._snapshot.retry_available,
            "active": self._snapshot.active,
            "ready": self._snapshot.ready,
            "process_id": self._snapshot.process_id,
            "updated_at": time.time(),
        }
        values.update(updates)
        self._set_state_locked(ProvisioningSnapshot(**values))

    def _set_state_locked(self, snapshot: ProvisioningSnapshot) -> None:
        self._snapshot = snapshot
        try:
            self.state_store.save(snapshot)
        except OSError:
            self.logger.exception("Provisioning state could not be persisted")
        callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(snapshot)
            except Exception:
                self.logger.exception("Provisioning state listener failed")


def _phase_for_progress(
    stage: str,
    downloaded: int | None,
    total: int | None,
) -> ProvisioningPhase:
    lowered = stage.casefold()
    if stage == "Checking for Ollama":
        return ProvisioningPhase.CHECKING_OLLAMA
    if "ollama" in lowered and "download" in lowered:
        return ProvisioningPhase.INSTALLING_OLLAMA
    if stage == "Verifying Ollama API":
        return ProvisioningPhase.STARTING_OLLAMA
    if stage == "Verifying installed model":
        return ProvisioningPhase.VERIFYING_MODEL
    if stage == "Testing minimal inference":
        return ProvisioningPhase.TESTING_INFERENCE
    if downloaded is not None or total is not None or "pull" in lowered:
        return ProvisioningPhase.DOWNLOADING_MODEL
    if "preparing" in lowered or "already installed" in lowered:
        return ProvisioningPhase.CHECKING_MODEL
    if stage == "Intelligent revision is ready":
        return ProvisioningPhase.READY
    return ProvisioningPhase.CHECKING_MODEL


def run_model_provisioning(config: OfflineWritingConfig | None = None) -> int:
    from PySide6.QtCore import QObject, Qt, Signal
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

    from offline_writing_reviser.logging_config import configure_logging
    from offline_writing_reviser.settings import SettingsStore
    from offline_writing_reviser.windows.control import WindowsControlServer
    from offline_writing_reviser.windows.single_instance import WindowsSingleInstance

    config = config or SettingsStore().load()
    configure_logging(config.log_file)
    logger = logging.getLogger("offline-writing-reviser")
    instance = WindowsSingleInstance(PROVISIONING_MUTEX_NAME)
    if not instance.acquire():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if send_provisioning_show_command():
                logger.info("Existing model setup window focused")
                instance.release()
                return 0
            time.sleep(0.1)
        logger.error("Existing model setup process has no control endpoint")
        instance.release()
        return 1

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    controller = ProvisioningController(config, logger=logger)

    class AccessibleProvisioningDialog(QDialog):
        allow_close = False

        def reject(self) -> None:
            if self.allow_close:
                super().reject()
                return
            self.hide()

        def closeEvent(self, event) -> None:
            if self.allow_close:
                super().closeEvent(event)
                return
            self.hide()
            event.ignore()

    dialog = AccessibleProvisioningDialog()
    dialog.setWindowTitle("Offline Writing Reviser - AI Setup")
    dialog.setAccessibleName("Intelligent revision setup")
    dialog.setMinimumWidth(500)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    layout = QVBoxLayout(dialog)
    label = QLabel("Ready to set up intelligent revision.")
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
    close_button = QPushButton("Hide")
    close_button.setAccessibleName("Hide or close AI setup")
    button_layout.addStretch()
    button_layout.addWidget(retry_button)
    button_layout.addWidget(cancel_button)
    button_layout.addWidget(close_button)
    layout.addWidget(label)
    layout.addWidget(detail)
    layout.addWidget(progress_bar)
    layout.addLayout(button_layout)

    latest = {"snapshot": controller.snapshot, "announcement": None}

    def announce(text: str) -> None:
        _announce_provisioning(dialog, text, logger)

    def render(snapshot: ProvisioningSnapshot) -> None:
        previous = latest["snapshot"]
        latest["snapshot"] = snapshot
        label.setText(snapshot.current_stage)
        if snapshot.latest_error:
            detail.setText(snapshot.latest_error)
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
        elif snapshot.total_bytes:
            downloaded = snapshot.downloaded_bytes or 0
            detail_text, percentage = _format_progress(
                downloaded, snapshot.total_bytes
            )
            detail.setText(detail_text)
            progress_bar.setRange(0, 1000)
            progress_bar.setValue(
                min(1000, int(downloaded * 1000 / snapshot.total_bytes))
            )
            progress_bar.setAccessibleDescription(
                f"{snapshot.current_stage}: {percentage} percent; "
                f"{_format_bytes(downloaded)} downloaded of "
                f"{_format_bytes(snapshot.total_bytes)}"
            )
        elif snapshot.ready:
            detail.setText(
                "Offline Writing Reviser is ready. Choose Close when finished."
            )
            progress_bar.setRange(0, 100)
            progress_bar.setValue(100)
        elif snapshot.active:
            detail.setText(
                "Provisioning is active. You may hide this window and reopen "
                "it later."
            )
            progress_bar.setRange(0, 0)
        else:
            detail.setText("")
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
        retry_button.setEnabled(snapshot.retry_available and not snapshot.active)
        cancel_button.setEnabled(
            snapshot.active
            and snapshot.phase is not ProvisioningPhase.INSTALLING_OLLAMA
        )
        close_button.setText("Hide" if snapshot.active else "Close")
        announcement_key = (
            snapshot.phase,
            snapshot.current_stage,
            snapshot.percentage,
        )
        should_announce = (
            snapshot.phase is ProvisioningPhase.READY
            or snapshot.phase is ProvisioningPhase.FAILED
            or snapshot.current_stage != previous.current_stage
            or (
                snapshot.percentage is not None
                and (
                    previous.percentage is None
                    or abs(snapshot.percentage - previous.percentage) >= 5
                )
            )
        )
        if should_announce and announcement_key != latest["announcement"]:
            message = snapshot.current_stage
            if snapshot.ready:
                message = "Offline Writing Reviser is ready."
            elif snapshot.latest_error:
                message = f"{message} {snapshot.latest_error}"
            elif snapshot.percentage is not None:
                message = f"{message}. {snapshot.percentage} percent."
            announce(message)
            latest["announcement"] = announcement_key

    class Bridge(QObject):
        state_changed = Signal(object)
        show_requested = Signal()

    bridge = Bridge()
    bridge.state_changed.connect(render)

    def show_dialog() -> None:
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                widget.showNormal()
                widget.raise_()
                widget.activateWindow()
                return
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
        label.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        render(controller.snapshot)

    bridge.show_requested.connect(show_dialog)
    controller.subscribe(bridge.state_changed.emit)

    server = WindowsControlServer(
        on_settings=lambda: None,
        on_exit=lambda: None,
        on_restart=lambda: None,
        logger=logger,
        window_class=PROVISIONING_CONTROL_WINDOW_CLASS,
        window_title=PROVISIONING_CONTROL_WINDOW_TITLE,
        callbacks={WM_PROVISIONING_SHOW: bridge.show_requested.emit},
    )

    def close_or_hide() -> None:
        if controller.snapshot.active:
            dialog.hide()
            return
        dialog.allow_close = True
        dialog.accept()
        app.quit()

    retry_button.clicked.connect(controller.start)
    cancel_button.clicked.connect(controller.cancel)
    close_button.clicked.connect(close_or_hide)
    try:
        server.start()
        if controller.snapshot.phase is ProvisioningPhase.IDLE:
            consent = QMessageBox.question(
                None,
                "Set up intelligent revision",
                f"Setup reuses a compatible Ollama installation when present "
                f"and otherwise downloads the official Ollama installer. It "
                f"then downloads {config.model} (approximately 3 GB, subject "
                "to the Gemma Terms of Use). The application installation does "
                "not depend on this step, but intelligent revision requires "
                "the model.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if consent != QMessageBox.StandardButton.Yes:
                return 0
        dialog.show()
        show_dialog()
        if controller.snapshot.phase is ProvisioningPhase.IDLE:
            controller.start()
        app.exec()
        return 0 if controller.snapshot.ready else 1
    finally:
        server.stop()
        instance.release()


def send_provisioning_show_command() -> bool:
    from offline_writing_reviser.windows.control import send_window_command

    return send_window_command(
        PROVISIONING_CONTROL_WINDOW_CLASS,
        PROVISIONING_CONTROL_WINDOW_TITLE,
        WM_PROVISIONING_SHOW,
    )


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


def _format_progress(completed: int, total: int) -> tuple[str, int]:
    percentage = min(100, max(0, int(completed * 100 / total)))
    return (
        f"{_format_bytes(completed)} of {_format_bytes(total)} "
        f"({percentage}%)",
        percentage,
    )


def _announce_provisioning(target: Any, text: str, logger: logging.Logger) -> None:
    try:
        from PySide6 import QtGui

        event = QtGui.QAccessibleAnnouncementEvent(target, text)
        event.setPoliteness(
            QtGui.QAccessible.AnnouncementPoliteness.Polite
        )
        QtGui.QAccessible.updateAccessibility(event)
    except (AttributeError, RuntimeError, TypeError):
        logger.debug("Accessible provisioning announcement unavailable")
