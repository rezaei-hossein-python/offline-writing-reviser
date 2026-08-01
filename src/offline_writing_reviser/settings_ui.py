from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.desktop_status import UserMessage
from offline_writing_reviser.settings import SettingsValidationError, config_with_updates


@dataclass(frozen=True)
class AccessibleControl:
    name: str
    role: str
    tab_order: int


# This is the UI contract exercised by structural tests and live UIA inspection.
ACCESSIBLE_CONTROLS = (
    AccessibleControl("Model", "combo box", 1),
    AccessibleControl("Refresh installed models", "button", 2),
    AccessibleControl("Revision timeout", "spin box", 3),
    AccessibleControl("Maximum input length", "spin box", 4),
    AccessibleControl("Global hotkey", "edit", 5),
    AccessibleControl("Log location", "read-only edit", 6),
    AccessibleControl("Reset to defaults", "button", 7),
    AccessibleControl("Save settings", "button", 8),
    AccessibleControl("Cancel", "button", 9),
)


class SettingsWindow:
    """A single Qt Widgets window exposed through Windows UI Automation."""

    def __init__(
        self,
        config_getter: Callable[[], OfflineWritingConfig],
        save_callback: Callable[[OfflineWritingConfig], OfflineWritingConfig],
        reset_callback: Callable[[], OfflineWritingConfig],
        model_loader: Callable[[], list[str]],
        logger: logging.Logger | None = None,
    ):
        self.config_getter = config_getter
        self.save_callback = save_callback
        self.reset_callback = reset_callback
        self.model_loader = model_loader
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._app = None
        self._window = None
        self._bridge = None
        self._controls: dict[str, object] = {}
        self._running = False

    def run(self, stop_event: threading.Event) -> None:
        from PySide6 import QtCore, QtWidgets

        class DispatchBridge(QtCore.QObject):
            requested = QtCore.Signal(object)

            def __init__(self, execute_callback):
                super().__init__()
                self._execute_callback = execute_callback

            @QtCore.Slot(object)
            def execute(self, callback) -> None:
                self._execute_callback(callback)

        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self._running = True
        self._app.setApplicationName("Offline Writing Reviser")
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge = DispatchBridge(self._execute_callback)
        self._bridge.requested.connect(self._bridge.execute)

        queue_timer = QtCore.QTimer()
        queue_timer.timeout.connect(self._drain_queue)
        queue_timer.start(25)
        stop_timer = QtCore.QTimer()
        stop_timer.timeout.connect(
            lambda: self._stop_ui() if stop_event.is_set() else None
        )
        stop_timer.start(100)
        self._drain_queue()
        self._app.exec()
        self._window = None
        self._controls.clear()
        self._bridge = None
        self._app = None
        self._running = False

    def show(self) -> None:
        self.dispatch(self._show_on_ui_thread)

    def show_error(self, message: UserMessage) -> None:
        self.dispatch(lambda: self._show_error_dialog(message.title, message.message))

    def update_runtime_status(self, text: str) -> None:
        """Expose progress through the status label and screen-reader events."""
        def update() -> None:
            if self._window and "status" in self._controls:
                self._set_status(text)
            self._announce_accessibly(text)

        self.dispatch(update)

    def _announce_accessibly(self, text: str) -> None:
        if not self._app:
            return
        try:
            from PySide6 import QtGui

            target = self._window or self._app
            event = QtGui.QAccessibleAnnouncementEvent(target, text)
            event.setPoliteness(
                QtGui.QAccessible.AnnouncementPoliteness.Polite
            )
            QtGui.QAccessible.updateAccessibility(event)
        except (AttributeError, RuntimeError, TypeError):
            self.logger.debug("Accessible status announcement unavailable")

    def close(self) -> None:
        if not self._running:
            return

        def close_application() -> None:
            self._stop_ui()

        self.dispatch(close_application)

    def _stop_ui(self) -> None:
        self._close_window()
        if self._app:
            self._app.quit()

    def dispatch(self, callback: Callable[[], None]) -> None:
        bridge = self._bridge
        if bridge:
            bridge.requested.emit(callback)
        else:
            self._queue.put(callback)

    def _execute_callback(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            self.logger.exception("Settings UI action failed")

    def _drain_queue(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                return
            self._execute_callback(callback)

    def _show_on_ui_thread(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets

        if self._window:
            self._window.showNormal()
            self._window.raise_()
            self._window.activateWindow()
            self._controls["model"].setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
            return

        owner = self

        class AccessibleSettingsDialog(QtWidgets.QDialog):
            def closeEvent(self, event: QtGui.QCloseEvent) -> None:
                owner._window = None
                owner._controls.clear()
                event.accept()

        config = self.config_getter()
        window = AccessibleSettingsDialog()
        self._window = window
        window.setWindowTitle("Offline Writing Reviser Settings")
        window.setAccessibleName("Offline Writing Reviser Settings")
        window.setModal(False)
        window.setMinimumWidth(640)

        layout = QtWidgets.QVBoxLayout(window)
        form = QtWidgets.QGridLayout()
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        model_label = QtWidgets.QLabel("&Model:")
        model = QtWidgets.QComboBox()
        model.setAccessibleName("Model")
        model.setAccessibleDescription(
            "Select an Ollama model already installed on this computer."
        )
        model_label.setBuddy(model)
        refresh = QtWidgets.QPushButton("&Refresh installed models")
        refresh.setAccessibleName("Refresh installed models")
        form.addWidget(model_label, 0, 0)
        form.addWidget(model, 0, 1)
        form.addWidget(refresh, 0, 2)

        timeout_label = QtWidgets.QLabel("&Revision timeout:")
        timeout = QtWidgets.QDoubleSpinBox()
        timeout.setAccessibleName("Revision timeout")
        timeout.setAccessibleDescription("Revision timeout in seconds.")
        timeout.setRange(5, 600)
        timeout.setDecimals(0)
        timeout.setSingleStep(1)
        timeout.setSuffix(" seconds")
        timeout_label.setBuddy(timeout)
        form.addWidget(timeout_label, 1, 0)
        form.addWidget(timeout, 1, 1)

        maximum_label = QtWidgets.QLabel("Ma&ximum input length:")
        maximum = QtWidgets.QSpinBox()
        maximum.setAccessibleName("Maximum input length")
        maximum.setAccessibleDescription("Maximum selected text length in characters.")
        maximum.setRange(100, 100_000)
        maximum.setSingleStep(100)
        maximum.setSuffix(" characters")
        maximum_label.setBuddy(maximum)
        form.addWidget(maximum_label, 2, 0)
        form.addWidget(maximum, 2, 1)

        hotkey_label = QtWidgets.QLabel("&Intelligent revision hotkey:")
        hotkey = QtWidgets.QLineEdit()
        hotkey.setAccessibleName("Global hotkey")
        hotkey.setAccessibleDescription(
            "Hotkey for intelligent revision. Use Ctrl and/or Alt plus "
            "one letter or number."
        )
        hotkey_label.setBuddy(hotkey)
        form.addWidget(hotkey_label, 3, 0)
        form.addWidget(hotkey, 3, 1)

        log_label = QtWidgets.QLabel("&Log location:")
        log_location = QtWidgets.QLineEdit()
        log_location.setAccessibleName("Log location")
        log_location.setAccessibleDescription(
            "Read-only folder containing application logs."
        )
        log_location.setReadOnly(True)
        log_label.setBuddy(log_location)
        form.addWidget(log_label, 4, 0)
        form.addWidget(log_location, 4, 1, 1, 2)

        status = QtWidgets.QLabel("Settings are ready.")
        status.setAccessibleName("Settings status")
        status.setWordWrap(True)
        status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(status)

        buttons = QtWidgets.QDialogButtonBox()
        reset = buttons.addButton(
            "Reset to &defaults", QtWidgets.QDialogButtonBox.ButtonRole.ResetRole
        )
        save = buttons.addButton(
            "&Save settings", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole
        )
        cancel = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        reset.setAccessibleName("Reset to defaults")
        save.setAccessibleName("Save settings")
        cancel.setAccessibleName("Cancel")
        save.setDefault(True)
        layout.addWidget(buttons)

        self._controls = {
            "model": model,
            "refresh": refresh,
            "timeout": timeout,
            "maximum": maximum,
            "hotkey": hotkey,
            "log_location": log_location,
            "status": status,
            "reset": reset,
            "save": save,
            "cancel": cancel,
        }
        self._set_tab_order(window)
        self._set_values(config)

        refresh.clicked.connect(self._refresh_models)
        reset.clicked.connect(self._reset)
        save.clicked.connect(self._save)
        cancel.clicked.connect(self._close_window)
        window.rejected.connect(self._close_window)

        window.adjustSize()
        window.show()
        # A source background process may have STARTUPINFO=SW_HIDE. Defer the
        # explicit user-requested activation until after Qt processes its first
        # show request so --settings is never consumed by that startup flag.
        QtCore.QTimer.singleShot(0, self._activate_window)
        self._refresh_models()

    def _activate_window(self) -> None:
        from PySide6 import QtCore

        if not self._window:
            return
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()
        self._controls["model"].setFocus(
            QtCore.Qt.FocusReason.ActiveWindowFocusReason
        )

    def _set_tab_order(self, window) -> None:
        names = [
            "model",
            "refresh",
            "timeout",
            "maximum",
            "hotkey",
            "log_location",
            "reset",
            "save",
            "cancel",
        ]
        for first, second in zip(names, names[1:]):
            window.setTabOrder(self._controls[first], self._controls[second])

    def _save(self) -> None:
        try:
            candidate = config_with_updates(
                self.config_getter(),
                model=self._controls["model"].currentText(),
                timeout_seconds=self._controls["timeout"].value(),
                max_characters=self._controls["maximum"].value(),
                hotkey=self._controls["hotkey"].text(),
            )
            applied = self.save_callback(candidate)
        except (ValueError, SettingsValidationError) as exc:
            self._validation_error(self._validation_control(str(exc)), str(exc))
            return
        except Exception:
            self.logger.exception("Settings could not be saved")
            self._validation_error(
                "save", "Settings could not be saved. See the application log."
            )
            return
        self._set_values(applied)
        self._close_window()

    def _reset(self) -> None:
        try:
            self._set_values(self.reset_callback())
            self._set_status("Default settings restored.")
            self._refresh_models()
        except Exception:
            self.logger.exception("Settings reset failed")
            self._show_error_dialog(
                "Settings error",
                "Default settings could not be restored. See the application log.",
            )

    def _refresh_models(self) -> None:
        refresh = self._controls["refresh"]
        refresh.setEnabled(False)
        self._set_status("Checking locally installed Ollama models.")

        def load() -> None:
            try:
                models = self.model_loader()
                error = None
            except Exception as exc:
                models = []
                error = exc
            self.dispatch(lambda: self._finish_model_refresh(models, error))

        threading.Thread(
            target=load, name="ollama-model-discovery", daemon=True
        ).start()

    def _finish_model_refresh(
        self, models: list[str], error: Exception | None
    ) -> None:
        if not self._window:
            return
        self._controls["refresh"].setEnabled(True)
        model = self._controls["model"]
        current = model.currentText()
        choices = list(models)
        if current and current not in choices:
            choices.insert(0, current)
        model.clear()
        model.addItems(choices)
        if current:
            model.setCurrentText(current)
        if error:
            self._set_status(
                "Ollama is unavailable. Start or install Ollama, then refresh."
            )
        elif current not in models:
            self._set_status(
                "The configured model is not installed. Select an installed model."
            )
        else:
            self._set_status(f"{len(models)} installed model(s) found.")

    def _set_values(self, config: OfflineWritingConfig) -> None:
        model = self._controls["model"]
        model.clear()
        model.addItem(config.model)
        model.setCurrentText(config.model)
        self._controls["timeout"].setValue(config.timeout_seconds)
        self._controls["maximum"].setValue(config.max_characters)
        self._controls["hotkey"].setText(config.hotkey)
        self._controls["log_location"].setText(str(config.log_file.parent))

    def _set_status(self, text: str) -> None:
        self._controls["status"].setText(text)

    def _validation_error(self, control_name: str, message: str) -> None:
        from PySide6 import QtCore

        self._set_status(f"Error: {message}")
        control = self._controls[control_name]
        control.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        self._show_error_dialog("Invalid settings", message)
        if self._window:
            control.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _show_error_dialog(self, title: str, message: str) -> None:
        from PySide6 import QtWidgets

        QtWidgets.QMessageBox.critical(self._window, title, message)

    @staticmethod
    def _validation_control(message: str) -> str:
        lowered = message.lower()
        if "timeout" in lowered:
            return "timeout"
        if "maximum" in lowered:
            return "maximum"
        if "hotkey" in lowered:
            return "hotkey"
        if "model" in lowered:
            return "model"
        return "save"

    def _close_window(self) -> None:
        if self._window and hasattr(self._window, "close"):
            window = self._window
            self._window = None
            self._controls.clear()
            window.close()
        elif self._window and hasattr(self._window, "destroy"):
            self._window.destroy()
            self._window = None
