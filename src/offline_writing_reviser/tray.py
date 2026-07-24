from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from offline_writing_reviser.desktop_status import ApplicationState, UserMessage
from offline_writing_reviser.paths import resource_path


class TrayIcon:
    def __init__(
        self,
        *,
        on_revise: Callable[[], None],
        on_settings: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_restart: Callable[[], None],
        on_exit: Callable[[], None],
        logger: logging.Logger | None = None,
    ):
        self.on_revise = on_revise
        self.on_settings = on_settings
        self.on_open_logs = on_open_logs
        self.on_restart = on_restart
        self.on_exit = on_exit
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self.state = ApplicationState.READY
        self._icon = None

    def start(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda _item: f"Status: {self.state.value}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Revise selected text", self._invoke(self.on_revise)),
            pystray.MenuItem(
                "Settings",
                self._invoke(self.on_settings),
                default=True,
            ),
            pystray.MenuItem("Open log folder", self._invoke(self.on_open_logs)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart", self._invoke(self.on_restart)),
            pystray.MenuItem("Exit", self._invoke(self.on_exit)),
        )
        self._icon = pystray.Icon(
            "OfflineWritingReviser",
            _load_icon_image(),
            self._tooltip(),
            menu,
        )
        self._icon.run_detached()
        self.logger.info("System tray icon started")

    def set_state(self, state: ApplicationState) -> None:
        self.state = state
        if self._icon:
            self._icon.title = self._tooltip()
            self._icon.update_menu()

    def notify(self, user_message: UserMessage) -> None:
        if not self._icon:
            return
        try:
            self._icon.notify(user_message.message, user_message.title)
        except Exception:
            self.logger.exception("Windows notification failed")

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
            self._icon = None
            self.logger.info("System tray icon stopped")

    def _tooltip(self) -> str:
        return f"Offline Writing Reviser — {self.state.value}"

    @staticmethod
    def _invoke(callback: Callable[[], None]):
        def invoke(_icon, _item) -> None:
            callback()

        return invoke


def _load_icon_image():
    from PIL import Image, ImageDraw

    icon_path = resource_path(Path("assets") / "offline-writing-reviser.ico")
    if icon_path.exists():
        return Image.open(icon_path)
    image = Image.new("RGBA", (64, 64), "#174a7e")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 58, 58), radius=12, fill="#174a7e")
    draw.line((17, 18, 45, 18), fill="white", width=5)
    draw.line((17, 31, 39, 31), fill="white", width=5)
    draw.line((17, 44, 33, 44), fill="white", width=5)
    draw.line((42, 42, 53, 31), fill="#7fd1ae", width=5)
    return image
