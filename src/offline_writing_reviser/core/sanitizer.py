from __future__ import annotations

import difflib
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
LINE_BREAK_PATTERN = re.compile(r"\r\n|\r|\n")
BULLET_PATTERN = re.compile(r"^(?P<marker>\s*(?:[-*+]|\d+[.)])\s+)")
LEADING_WHITESPACE_PATTERN = re.compile(r"^\s*")
TRAILING_WHITESPACE_PATTERN = re.compile(r"\s*$")
REPEATED_SPACING_PATTERN = re.compile(r"[ \t]{2,}")
COMMENTARY_PREFIXES = (
    "here is the corrected text:",
    "here's the corrected text:",
    "here is the revised text:",
    "here's the revised text:",
    "here is the revision:",
    "here's the revision:",
    "corrected text:",
    "revised text:",
    "the sentence is already correct",
    "the text is already correct",
    "no correction is needed",
    "no corrections are needed",
    "okay, please provide",
    "okay, here is",
    "okay, i corrected",
    "sure, here is",
    "sure, i can",
    "i have corrected",
    "i corrected",
    "revision:",
    "explanation:",
    "analysis:",
    "reasoning:",
    "score:",
)
TYPOGRAPHIC_APOSTROPHES = ("\u2018", "\u2019")
TYPOGRAPHIC_DOUBLE_QUOTES = ("\u201c", "\u201d")
PRESERVED_DASHES = ("\u2013", "\u2014")


def sanitize_revision_output(output: str, original_text: str | None = None) -> str:
    if "\x00" in output:
        raise OfflineWritingMalformedOutput("Local model returned null bytes")
    if "```" in output or MARKDOWN_FENCE_PATTERN.match(output):
        raise OfflineWritingMalformedOutput("Local model returned markdown fences")
    revised = ANSI_OSC_PATTERN.sub("", output)
    revised = ANSI_CSI_PATTERN.sub("", revised)
    revised = ANSI_ESCAPE_PATTERN.sub("", revised)
    if "\x1b" in revised:
        raise OfflineWritingMalformedOutput("Local model returned escape characters")
    revised = revised.replace("\r\n", "\n").replace("\r", "\n")
    revised = CONTROL_CHARACTER_PATTERN.sub("", revised)
    if original_text is None or original_text == original_text.strip():
        revised = revised.strip()
    if not revised:
        raise OfflineWritingMalformedOutput("Local model returned empty output")
    if _is_single_wrapped_quote(revised):
        original_is_wrapped = bool(
            original_text
            and _is_single_wrapped_quote(
                original_text.replace("\r\n", "\n").replace("\r", "\n")
            )
        )
        if not original_is_wrapped:
            revised = revised[1:-1].strip()
    if not revised:
        raise OfflineWritingMalformedOutput("Local model returned empty output")
    if _looks_like_commentary(revised, original_text):
        raise OfflineWritingMalformedOutput("Local model returned commentary")
    if original_text is not None:
        revised = _preserve_source_typography(revised, original_text)
        revised = _validate_and_restore_structure(revised, original_text)
        _validate_minimal_edit(revised, original_text)
    _validate_revised_text(revised, original_text=original_text)
    return revised


def _is_single_wrapped_quote(value: str) -> bool:
    return bool(re.match(r"""^(['"]).*\1$""", value, flags=re.DOTALL))


def _looks_like_commentary(revised: str, original_text: str | None) -> bool:
    lowered = revised.lstrip().casefold()
    original_lowered = (
        original_text.lstrip().casefold() if original_text is not None else ""
    )
    for prefix in COMMENTARY_PREFIXES:
        if lowered.startswith(prefix) and not original_lowered.startswith(prefix):
            return True
    if original_text is None and lowered.startswith(("here is", "here's", "revision:")):
        return True
    return False


def _preserve_source_typography(revised: str, original_text: str) -> str:
    preserved = revised
    if "'" in original_text and not any(
        character in original_text for character in TYPOGRAPHIC_APOSTROPHES
    ):
        preserved = preserved.translate(
            {ord(character): "'" for character in TYPOGRAPHIC_APOSTROPHES}
        )
    if '"' in original_text and not any(
        character in original_text for character in TYPOGRAPHIC_DOUBLE_QUOTES
    ):
        preserved = preserved.translate(
            {ord(character): '"' for character in TYPOGRAPHIC_DOUBLE_QUOTES}
        )

    if '"' in original_text and preserved.count('"') != original_text.count('"'):
        raise OfflineWritingMalformedOutput("Local model changed quotation structure")
    for character in TYPOGRAPHIC_DOUBLE_QUOTES:
        if preserved.count(character) != original_text.count(character):
            raise OfflineWritingMalformedOutput("Local model changed quotation typography")
    for character in PRESERVED_DASHES:
        if preserved.count(character) != original_text.count(character):
            raise OfflineWritingMalformedOutput("Local model changed dash typography")
    return preserved


def _validate_and_restore_structure(revised: str, original_text: str) -> str:
    original_breaks = LINE_BREAK_PATTERN.findall(original_text)
    revised_lines = revised.split("\n")
    original_lines = LINE_BREAK_PATTERN.split(original_text)
    if len(revised_lines) != len(original_lines):
        raise OfflineWritingMalformedOutput("Local model changed line-break structure")

    for original_line, revised_line in zip(original_lines, revised_lines, strict=True):
        if bool(original_line.strip()) != bool(revised_line.strip()):
            raise OfflineWritingMalformedOutput("Local model changed blank-line structure")
        if _leading_whitespace(original_line) != _leading_whitespace(revised_line):
            raise OfflineWritingMalformedOutput("Local model changed line indentation")
        if _trailing_whitespace(original_line) != _trailing_whitespace(revised_line):
            raise OfflineWritingMalformedOutput("Local model changed line spacing")
        if REPEATED_SPACING_PATTERN.findall(
            original_line.strip()
        ) != REPEATED_SPACING_PATTERN.findall(revised_line.strip()):
            raise OfflineWritingMalformedOutput("Local model normalized spacing")
        original_bullet = BULLET_PATTERN.match(original_line)
        revised_bullet = BULLET_PATTERN.match(revised_line)
        if bool(original_bullet) != bool(revised_bullet):
            raise OfflineWritingMalformedOutput("Local model changed list structure")
        if (
            original_bullet
            and revised_bullet
            and original_bullet.group("marker") != revised_bullet.group("marker")
        ):
            raise OfflineWritingMalformedOutput("Local model changed list markers")

    pieces: list[str] = []
    for index, line in enumerate(revised_lines):
        pieces.append(line)
        if index < len(original_breaks):
            pieces.append(original_breaks[index])
    return "".join(pieces)


def _leading_whitespace(value: str) -> str:
    return LEADING_WHITESPACE_PATTERN.match(value).group(0)


def _trailing_whitespace(value: str) -> str:
    return TRAILING_WHITESPACE_PATTERN.search(value).group(0)


def _validate_minimal_edit(revised: str, original_text: str) -> None:
    original_length = max(len(original_text), 1)
    revised_length = len(revised)
    if original_length >= 20 and revised_length < original_length * 0.6:
        raise OfflineWritingMalformedOutput("Local model output was suspiciously short")
    if original_length >= 20 and revised_length > original_length * 1.6:
        raise OfflineWritingMalformedOutput("Local model output was suspiciously long")
    if abs(revised_length - original_length) > max(80, original_length * 0.5):
        raise OfflineWritingMalformedOutput("Local model output changed length excessively")
    similarity = difflib.SequenceMatcher(None, original_text, revised).ratio()
    if original_length >= 20 and similarity < 0.55:
        raise OfflineWritingMalformedOutput("Local model output was not a minimal edit")


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
