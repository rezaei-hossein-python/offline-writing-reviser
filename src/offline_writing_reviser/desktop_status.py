from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from offline_writing_reviser.core.errors import (
    OfflineWritingCancelled,
    OfflineWritingCorrectionUnavailable,
    OfflineWritingInputError,
)
from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.provisioning_state import ProvisioningStateStore


class ApplicationState(str, Enum):
    READY = "Ready"
    REVISING = "Revising"
    OLLAMA_UNAVAILABLE = "Ollama unavailable"
    MODEL_UNAVAILABLE = "Model unavailable"
    HOTKEY_UNAVAILABLE = "Hotkey unavailable"
    ERROR = "Error"


@dataclass(frozen=True)
class UserMessage:
    title: str
    message: str
    state: ApplicationState


StateListener = Callable[[ApplicationState], None]


def user_message_for_error(error: BaseException | str) -> UserMessage:
    if isinstance(error, OfflineWritingCancelled):
        return UserMessage(
            "Cancelled",
            "The revision was cancelled and the selected text was not changed.",
            ApplicationState.READY,
        )
    if isinstance(error, OfflineWritingCorrectionUnavailable):
        return UserMessage(
            "Grammar correction unavailable",
            "The private grammar service could not start. Restart Offline "
            "Writing Reviser and try Ctrl+Alt+P again. If the problem "
            "continues, reinstall the application.",
            ApplicationState.ERROR,
        )
    if error == "no_selection":
        return UserMessage(
            "No text selected",
            "Select some text in the active application and try again.",
            ApplicationState.READY,
        )
    if error == "capture_failed":
        return UserMessage(
            "Selection could not be captured",
            "Keep the target application active and try the hotkey again.",
            ApplicationState.READY,
        )
    if error == "focus_changed":
        return UserMessage(
            "Replacement cancelled",
            "The active window changed, so the revised text was not pasted.",
            ApplicationState.READY,
        )
    if error == "clipboard_busy":
        return UserMessage(
            "Clipboard unavailable",
            "Another application is using the clipboard. Wait a moment and "
            "try again.",
            ApplicationState.READY,
        )
    if error == "paste_failed":
        return UserMessage(
            "Revised text could not be pasted",
            "Keep the target application active and try the hotkey again.",
            ApplicationState.READY,
        )
    if error == "hotkey":
        return UserMessage(
            "Hotkey unavailable",
            "That global hotkey is already in use or could not be registered.",
            ApplicationState.HOTKEY_UNAVAILABLE,
        )
    if isinstance(
        error, (OfflineWritingModelMissing, OfflineWritingProviderUnavailable)
    ) and ProvisioningStateStore().is_active():
        return UserMessage(
            "AI model setup in progress",
            "AI model setup is still in progress. Open Model Setup to view "
            "progress.",
            ApplicationState.MODEL_UNAVAILABLE,
        )
    if isinstance(error, OfflineWritingProviderTimeout):
        return UserMessage(
            "Revision timed out",
            "The local model did not finish before the configured timeout.",
            ApplicationState.ERROR,
        )
    if isinstance(error, OfflineWritingModelMissing):
        return UserMessage(
            "Model not ready",
            "The AI model is not installed yet. Run Set up intelligent "
            "revision from the Start menu to download it, then retry "
            "Ctrl+Alt+P.",
            ApplicationState.MODEL_UNAVAILABLE,
        )
    if isinstance(error, OfflineWritingProviderUnavailable):
        return UserMessage(
            "Ollama unavailable",
            "Run Set up intelligent revision from the Start menu to install or repair "
            "the local AI engine.",
            ApplicationState.OLLAMA_UNAVAILABLE,
        )
    if isinstance(error, OfflineWritingInputError):
        return UserMessage(
            "Selection cannot be revised",
            str(error),
            ApplicationState.ERROR,
        )
    if isinstance(error, OfflineWritingProviderError):
        return UserMessage(
            "Revision failed",
            "The local model could not revise the selected text. "
            "See Diagnostics for details.",
            ApplicationState.ERROR,
        )
    return UserMessage(
        "Revision failed",
        "The revision could not be completed. See Diagnostics for details.",
        ApplicationState.ERROR,
    )
