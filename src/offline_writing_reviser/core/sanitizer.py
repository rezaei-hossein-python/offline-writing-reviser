from __future__ import annotations

import re
import unicodedata

from offline_writing_reviser.core.errors import OfflineWritingMalformedOutput


ANSI_CSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b[@-Z\\-_]")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MARKDOWN_FENCE_PATTERN = re.compile(
    r"^\s*```[^\r\n]*\r?\n(?P<body>.*?)(?:\r?\n)?```\s*$",
    re.DOTALL,
)


def sanitize_revision_output(output: str, original_text: str | None = None) -> str:
    if "\x00" in output:
        raise OfflineWritingMalformedOutput("Local model returned null bytes")
    revised = ANSI_OSC_PATTERN.sub("", output)
    revised = ANSI_CSI_PATTERN.sub("", revised)
    revised = ANSI_ESCAPE_PATTERN.sub("", revised)
    if "\x1b" in revised:
        raise OfflineWritingMalformedOutput("Local model returned escape characters")
    revised = revised.replace("\r\n", "\n").replace("\r", "\n")
    fenced = MARKDOWN_FENCE_PATTERN.match(revised)
    if fenced:
        revised = fenced.group("body")
    revised = CONTROL_CHARACTER_PATTERN.sub("", revised)
    revised = revised.strip()
    if not revised:
        raise OfflineWritingMalformedOutput("Local model returned empty output")
    if "```" in revised:
        raise OfflineWritingMalformedOutput("Local model returned markdown fences")
    lowered = revised.lower()
    prohibited_prefixes = (
        "here is",
        "here's",
        "revised text:",
        "revision:",
        "explanation:",
        "score:",
    )
    if lowered.startswith(prohibited_prefixes):
        raise OfflineWritingMalformedOutput("Local model returned commentary")
    if _is_single_wrapped_quote(revised):
        revised = revised[1:-1].strip()
    if not revised:
        raise OfflineWritingMalformedOutput("Local model returned empty output")
    _validate_revised_text(revised, original_text=original_text)
    return revised


def _is_single_wrapped_quote(value: str) -> bool:
    return bool(re.match(r"""^(['"]).*\1$""", value, flags=re.DOTALL))


def _validate_revised_text(revised: str, original_text: str | None = None) -> None:
    if not revised.strip():
        raise OfflineWritingMalformedOutput("Local model returned empty output")
    if "\x00" in revised or "\x1b" in revised or CONTROL_CHARACTER_PATTERN.search(revised):
        raise OfflineWritingMalformedOutput("Local model returned control characters")
    try:
        revised.encode("utf-8")
    except UnicodeError as exc:
        raise OfflineWritingMalformedOutput("Local model returned invalid Unicode") from exc
    for character in revised:
        if unicodedata.category(character) == "Cs":
            raise OfflineWritingMalformedOutput("Local model returned invalid Unicode")
    if original_text is not None:
        original_len = max(len(original_text.strip()), 1)
        max_len = max(original_len * 4, original_len + 500)
        if len(revised) > max_len:
            raise OfflineWritingMalformedOutput("Local model output was suspiciously long")
