from __future__ import annotations


class OfflineWritingError(RuntimeError):
    pass


class OfflineWritingBusy(OfflineWritingError):
    pass


class OfflineWritingInputError(OfflineWritingError):
    pass


class OfflineWritingCancelled(OfflineWritingError):
    pass


class OfflineWritingMalformedOutput(OfflineWritingError):
    def __init__(
        self,
        message: str = "Local model returned unusable output",
        *,
        reason: str = "malformed_output",
    ):
        super().__init__(message)
        self.reason = reason
