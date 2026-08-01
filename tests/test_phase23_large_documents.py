from __future__ import annotations

import threading
from collections import deque

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.chunking import split_proofreading_chunks
from offline_writing_reviser.core.errors import OfflineWritingCancelled
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.providers.base import (
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)


class SequenceProvider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, responses=(), *, available=True):
        self.responses = deque(responses)
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def revise(self, text, instruction, timeout_seconds):
        self.calls.append((text, instruction, timeout_seconds))
        response = self.responses.popleft() if self.responses else text
        if isinstance(response, BaseException):
            raise response
        return response


def test_paragraph_aware_chunking_preserves_blank_lines_and_crlf():
    source = "First paragraph.\r\n\r\nSecond paragraph.\r\n\r\nThird paragraph."
    chunks = split_proofreading_chunks(source, 25)

    assert chunks == [
        "First paragraph.\r\n\r\n",
        "Second paragraph.\r\n\r\n",
        "Third paragraph.",
    ]
    assert "".join(chunks) == source


def test_sentence_then_clause_fallback_chunking():
    source = "A short sentence. Another clause, with more words; and its ending."
    chunks = split_proofreading_chunks(source, 25)

    assert chunks[0] == "A short sentence. "
    assert "".join(chunks) == source
    assert all(chunk for chunk in chunks)


@pytest.mark.parametrize(
    "token",
    [
        "https://example.com/a/very/long/path?item=42",
        "editorial-team@example.com",
        "2026-09-15T09:30:45",
        "OWR-PROTECTED-2048",
    ],
)
def test_chunking_never_splits_protected_tokens(token):
    source = f"Before {token} after several ordinary words."
    chunks = split_proofreading_chunks(source, 18)

    assert "".join(chunks) == source
    assert sum(token in chunk for chunk in chunks) == 1


def test_lists_indentation_headings_and_quotes_are_preserved():
    source = "# Plan\r\n  1. First item.\r\n  - Second item.\r\n> Quoted text."
    provider = SequenceProvider([source])
    result = OfflineWritingService(provider).revise(source)

    assert result.revised_text == source
    assert result.revised_text.splitlines()[0] == "# Plan"
    assert result.revised_text.splitlines()[1].startswith("  1. ")
    assert result.revised_text.splitlines()[2].startswith("  - ")
    assert result.revised_text.splitlines()[3].startswith("> ")


def test_successful_multi_chunk_reconstruction_and_progress():
    source = "I recieved the adress yesterday.\r\n\r\nHe go to work every day."
    provider = SequenceProvider(
        ["I received the address yesterday.", "He goes to work every day."]
    )
    progress = []
    service = OfflineWritingService(
        provider, OfflineWritingConfig(chunk_characters=38)
    )

    result = service.revise(source, progress=progress.append)

    assert result.revised_text == (
        "I received the address yesterday.\r\n\r\n"
        "He goes to work every day."
    )
    assert progress == ["Revising section 1 of 2", "Revising section 2 of 2", "Completed"]
    assert result.metadata["successful_chunks"] == 2


def test_unsafe_chunk_is_preserved_and_later_chunk_continues():
    source = "First sentence is correct.\n\nHe go to work every day."
    provider = SequenceProvider(
        ["Analysis: changed text", "He goes to work every day."]
    )
    service = OfflineWritingService(
        provider, OfflineWritingConfig(chunk_characters=29)
    )

    result = service.revise(source)

    assert result.revised_text == (
        "First sentence is correct.\n\nHe goes to work every day."
    )
    assert len(provider.calls) == 2
    assert result.metadata["unsafe_chunks"] == 1
    assert result.metadata["completion_status"] == "Completed with some sections unchanged"


def test_timeout_chunk_is_retried_preserved_and_later_chunk_continues():
    source = "First sentence is correct.\n\nHe go to work every day."
    timeout = OfflineWritingProviderTimeout("timed out")
    provider = SequenceProvider(
        [timeout, timeout, "He goes to work every day."]
    )
    service = OfflineWritingService(
        provider, OfflineWritingConfig(chunk_characters=29)
    )

    result = service.revise(source)

    assert result.revised_text.endswith("He goes to work every day.")
    assert len(provider.calls) == 3
    assert result.metadata["timeout_chunks"] == 1
    assert result.metadata["preserved_chunks"] == 1


def test_provider_unavailable_stops_operation():
    source = "First sentence is correct.\n\nSecond sentence is correct."
    provider = SequenceProvider(
        ["First sentence is correct.", OfflineWritingProviderUnavailable("gone")]
    )
    service = OfflineWritingService(
        provider, OfflineWritingConfig(chunk_characters=30)
    )

    with pytest.raises(OfflineWritingProviderUnavailable):
        service.revise(source)

    assert len(provider.calls) == 2


def test_cancellation_stops_before_any_partial_result():
    source = "First sentence.\n\nSecond sentence."
    provider = SequenceProvider()
    service = OfflineWritingService(
        provider, OfflineWritingConfig(chunk_characters=20)
    )

    def progress(_message):
        service.cancel()

    with pytest.raises(OfflineWritingCancelled):
        service.revise(source, progress=progress)

    assert provider.calls == []


def test_duplicate_revision_is_rejected_while_first_is_running():
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(SequenceProvider):
        def revise(self, text, instruction, timeout_seconds):
            entered.set()
            release.wait(2)
            return text

    service = OfflineWritingService(BlockingProvider())
    worker = threading.Thread(target=service.revise, args=("Correct sentence.",))
    worker.start()
    assert entered.wait(1)
    try:
        from offline_writing_reviser.core.errors import OfflineWritingBusy

        with pytest.raises(OfflineWritingBusy):
            service.revise("Another sentence.")
    finally:
        release.set()
        worker.join(2)


def test_long_mixed_error_document_preserves_complete_structure_and_anchors():
    paragraph = (
        "Alex does not approve invoice INV-2048 for $125 on September 15, 2026 "
        "at 9:30 AM; details remain at https://example.com/a and ops@example.com."
    )
    source = "\r\n\r\n".join(paragraph for _ in range(20))
    provider = SequenceProvider()
    service = OfflineWritingService(
        provider,
        OfflineWritingConfig(max_characters=100_000, chunk_characters=700),
    )

    result = service.revise(source)

    assert result.revised_text == source
    assert result.revised_text.count("\r\n\r\n") == 19
    assert result.revised_text.count("Alex") == 20
    assert result.revised_text.count("does not approve") == 20
    assert result.metadata["chunk_count"] > 1
