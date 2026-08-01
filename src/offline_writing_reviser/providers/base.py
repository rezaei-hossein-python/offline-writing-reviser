from __future__ import annotations

from abc import ABC, abstractmethod


class OfflineWritingProviderError(RuntimeError):
    pass


class OfflineWritingProviderUnavailable(OfflineWritingProviderError):
    pass


class OfflineWritingModelMissing(OfflineWritingProviderUnavailable):
    pass


class OfflineWritingProviderTimeout(OfflineWritingProviderError):
    pass


class OfflineWritingProviderCancelled(OfflineWritingProviderError):
    pass


class OfflineWritingProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_identifier(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def revise(self, text: str, instruction: str, timeout_seconds: float) -> str:
        raise NotImplementedError
