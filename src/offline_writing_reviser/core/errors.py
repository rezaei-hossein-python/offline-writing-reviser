from __future__ import annotations


class OfflineWritingError(RuntimeError):
    pass


class OfflineWritingBusy(OfflineWritingError):
    pass


class OfflineWritingInputError(OfflineWritingError):
    pass


class OfflineWritingMalformedOutput(OfflineWritingError):
    pass


class OfflineWritingLanguageToolUnavailable(OfflineWritingError):
    pass
