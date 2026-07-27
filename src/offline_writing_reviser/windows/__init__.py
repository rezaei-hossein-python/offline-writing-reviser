from __future__ import annotations

from typing import Any


__all__ = [
    "HotkeyBinding",
    "OfflineWritingController",
    "OfflineWritingRuntime",
    "SelectedTextCapture",
    "WindowsClipboard",
    "WindowsHotkeyManager",
    "WindowsSelectedTextAdapter",
    "start_offline_writing_runtime",
]


def __getattr__(name: str) -> Any:
    """Keep the public convenience imports without eager circular imports."""
    if name in {
        "OfflineWritingController",
        "OfflineWritingRuntime",
        "start_offline_writing_runtime",
    }:
        from offline_writing_reviser.windows import controller

        return getattr(controller, name)
    if name in {"HotkeyBinding", "WindowsHotkeyManager"}:
        from offline_writing_reviser.windows import hotkeys

        return getattr(hotkeys, name)
    if name in {
        "SelectedTextCapture",
        "WindowsClipboard",
        "WindowsSelectedTextAdapter",
    }:
        from offline_writing_reviser.windows import text_selection

        return getattr(text_selection, name)
    raise AttributeError(name)
