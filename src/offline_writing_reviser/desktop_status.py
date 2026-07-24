from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from offline_writing_reviser.core.errors import OfflineWritingInputError
from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


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
    if error == "no_selection":
        return UserMessage(
            "No text selected",
            "Select some text in the active application and try again.",
            ApplicationState.READY,
        )
    if error == "focus_changed":
        return UserMessage(
            "Replacement cancelled",
            "The active window changed, so the revised text was not pasted.",
            ApplicationState.READY,
        )
    if error == "hotkey":
        return UserMessage(
            "Hotkey unavailable",
            "That global hotkey is already in use or could not be registered.",
            ApplicationState.HOTKEY_UNAVAILABLE,
        )
    if isinstance(error, OfflineWritingProviderTimeout):
        return UserMessage(
            "Revision timed out",
            "The local model did not finish before the configured timeout.",
            ApplicationState.ERROR,
        )
    if isinstance(error, OfflineWritingModelMissing):
        return UserMessage(
            "Configured model unavailable",
            "Choose an installed Ollama model in Settings.",
            ApplicationState.MODEL_UNAVAILABLE,
        )
    if isinstance(error, OfflineWritingProviderUnavailable):
        return UserMessage(
            "Ollama unavailable",
            "Install or start Ollama, then refresh the model list in Settings.",
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
            "The local model could not revise the selected text. See the log for details.",
            ApplicationState.ERROR,
        )
    return UserMessage(
        "Revision failed",
        "The revision could not be completed. See the log for details.",
        ApplicationState.ERROR,
    )
