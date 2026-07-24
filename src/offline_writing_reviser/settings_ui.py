from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.settings import SettingsValidationError, config_with_updates


class SettingsWindow:
    """A single, keyboard-accessible native settings window."""

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
        self._root = None
        self._window = None
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()

    def run(self, stop_event: threading.Event) -> None:
        import tkinter as tk

        self._root = tk.Tk()
        self._root.withdraw()
        self._root.after(50, self._drain_queue)
        self._root.after(100, lambda: self._check_stop(stop_event))
        self._root.mainloop()
        self._root = None

    def show(self) -> None:
        self.dispatch(self._show_on_ui_thread)

    def close(self) -> None:
        def close_root() -> None:
            if self._window:
                self._window.destroy()
                self._window = None
            if self._root:
                self._root.quit()

        self.dispatch(close_root)

    def dispatch(self, callback: Callable[[], None]) -> None:
        self._queue.put(callback)

    def _drain_queue(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                self.logger.exception("Settings UI action failed")
        if self._root:
            self._root.after(50, self._drain_queue)

    def _check_stop(self, stop_event: threading.Event) -> None:
        if stop_event.is_set():
            self.close()
            return
        if self._root:
            self._root.after(100, lambda: self._check_stop(stop_event))

    def _show_on_ui_thread(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        if self._window and self._window.winfo_exists():
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return

        config = self.config_getter()
        window = tk.Toplevel(self._root)
        self._window = window
        window.title("Offline Writing Reviser Settings")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_window)
        window.bind("<Escape>", lambda _event: self._close_window())

        frame = ttk.Frame(window, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        model_var = tk.StringVar(value=config.model)
        timeout_var = tk.StringVar(value=str(config.timeout_seconds))
        maximum_var = tk.StringVar(value=str(config.max_characters))
        hotkey_var = tk.StringVar(value=config.hotkey)
        status_var = tk.StringVar(value="")

        ttk.Label(frame, text="Selected Ollama model:").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=5
        )
        model_combo = ttk.Combobox(
            frame,
            textvariable=model_var,
            width=35,
            state="readonly",
            takefocus=True,
        )
        model_combo.grid(row=0, column=1, sticky="ew", pady=5)
        refresh_button = ttk.Button(
            frame,
            text="Refresh models",
            command=lambda: refresh_models(),
            takefocus=True,
        )
        refresh_button.grid(row=0, column=2, padx=(8, 0), pady=5)

        ttk.Label(frame, text="Revision timeout (seconds):").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=5
        )
        timeout_entry = ttk.Spinbox(
            frame,
            from_=5,
            to=600,
            increment=1,
            textvariable=timeout_var,
            width=12,
            takefocus=True,
        )
        timeout_entry.grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(frame, text="Maximum input length:").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=5
        )
        maximum_entry = ttk.Spinbox(
            frame,
            from_=100,
            to=100000,
            increment=100,
            textvariable=maximum_var,
            width=12,
            takefocus=True,
        )
        maximum_entry.grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(frame, text="Global hotkey:").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=5
        )
        hotkey_entry = ttk.Entry(
            frame, textvariable=hotkey_var, width=20, takefocus=True
        )
        hotkey_entry.grid(row=3, column=1, sticky="w", pady=5)

        ttk.Label(frame, text="Log location:").grid(
            row=4, column=0, sticky="nw", padx=(0, 12), pady=5
        )
        log_label = ttk.Label(
            frame,
            text=str(config.log_file.parent),
            wraplength=390,
        )
        log_label.grid(row=4, column=1, columnspan=2, sticky="w", pady=5)

        ttk.Label(
            frame,
            textvariable=status_var,
            wraplength=520,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))

        def set_values(new_config: OfflineWritingConfig) -> None:
            model_var.set(new_config.model)
            timeout_var.set(str(new_config.timeout_seconds))
            maximum_var.set(str(new_config.max_characters))
            hotkey_var.set(new_config.hotkey)

        def save() -> None:
            try:
                candidate = config_with_updates(
                    self.config_getter(),
                    model=model_var.get(),
                    timeout_seconds=float(timeout_var.get()),
                    max_characters=int(maximum_var.get()),
                    hotkey=hotkey_var.get(),
                )
                applied = self.save_callback(candidate)
            except (ValueError, SettingsValidationError) as exc:
                messagebox.showerror("Invalid settings", str(exc), parent=window)
                return
            set_values(applied)
            self._close_window()

        def reset() -> None:
            set_values(self.reset_callback())
            status_var.set("Default settings restored.")
            refresh_models()

        def refresh_models() -> None:
            refresh_button.state(["disabled"])
            status_var.set("Checking locally installed Ollama models…")

            def load() -> None:
                try:
                    models = self.model_loader()
                    error = None
                except Exception as exc:
                    models = []
                    error = exc

                def finish() -> None:
                    refresh_button.state(["!disabled"])
                    current = model_var.get()
                    choices = list(models)
                    if current and current not in choices:
                        choices.insert(0, current)
                    model_combo["values"] = choices
                    if error:
                        status_var.set(
                            "Ollama is unavailable. Start or install Ollama, then refresh."
                        )
                    elif current not in models:
                        status_var.set(
                            "The configured model is not installed. Select an installed model."
                        )
                    else:
                        status_var.set(f"{len(models)} installed model(s) found.")

                self.dispatch(finish)

            threading.Thread(target=load, name="ollama-model-discovery", daemon=True).start()

        ttk.Button(
            buttons, text="Reset to defaults", command=reset, takefocus=True
        ).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(
            buttons, text="Cancel", command=self._close_window, takefocus=True
        ).grid(
            row=0, column=1, padx=(0, 8)
        )
        save_button = ttk.Button(
            buttons, text="Save", command=save, takefocus=True
        )
        save_button.grid(row=0, column=2)

        window.bind("<Return>", lambda _event: save())
        window.update_idletasks()
        window.geometry(
            f"+{max(0, (window.winfo_screenwidth() - window.winfo_width()) // 2)}"
            f"+{max(0, (window.winfo_screenheight() - window.winfo_height()) // 2)}"
        )
        model_combo.focus_set()
        refresh_models()

    def _close_window(self) -> None:
        if self._window:
            self._window.destroy()
            self._window = None
