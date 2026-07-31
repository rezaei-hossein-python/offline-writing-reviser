from __future__ import annotations

import re
import unicodedata

from offline_writing_reviser.core.errors import OfflineWritingMalformedOutput


ANSI_CSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b[@-Z\\-_]")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
MARKDOWN_LABEL_PATTERN = re.compile(
    r"^\s*(?:\*\*|__)(?:revised|corrected)\s+text(?:\*\*|__)\s*:?",
    re.IGNORECASE,
)
MARKDOWN_EMPHASIS_WRAPPER_PATTERN = re.compile(
    r"^\s*(?:\*\*.+\*\*|__.+__)\s*$", re.DOTALL
)
XML_WRAPPER_PATTERN = re.compile(
    r"^\s*(?:<\?xml\b[^>]*>\s*)?<([A-Za-z_][\w:.-]*)\b[^>]*>.*</\1>\s*$",
    re.DOTALL,
)
SAFE_LABEL_PATTERN = re.compile(
    r"^\s*(?:revised|corrected)\s+text\s*:\s*(?P<body>\S(?:.*\S)?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
PROMPT_ECHO_PHRASES = (
    "you are an expert english editor",
    "revise the supplied text so it is",
    "return only the final revised text",
    "do not invent facts",
)
COMMENTARY_PREFIXES = (
    "here is the corrected text:",
    "here's the corrected text:",
    "here is the revised text:",
    "here's the revised text:",
    "here is the revision:",
    "here's the revision:",
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


def sanitize_revision_output(output: str, original_text: str | None = None) -> str:
    if "\x00" in output:
        _reject("null_bytes", "Local model returned null bytes")
    if "```" in output:
        _reject("markdown_wrapper", "Local model returned Markdown fences")

    revised = ANSI_OSC_PATTERN.sub("", output)
    revised = ANSI_CSI_PATTERN.sub("", revised)
    revised = ANSI_ESCAPE_PATTERN.sub("", revised)
    if "\x1b" in revised:
        _reject("escape_characters", "Local model returned escape characters")
    revised = revised.replace("\r\n", "\n").replace("\r", "\n")
    revised = CONTROL_CHARACTER_PATTERN.sub("", revised)
    if original_text is None or original_text == original_text.strip():
        revised = revised.strip()
    if not revised:
        _reject("empty_output", "Local model returned empty output")

    label_match = SAFE_LABEL_PATTERN.fullmatch(revised)
    if label_match:
        revised = label_match.group("body").strip()

    if _is_single_wrapped_quote(revised) and not _is_single_wrapped_quote(
        _normalized(original_text or "")
    ):
        revised = revised[1:-1].strip()
    if not revised:
        _reject("empty_output", "Local model returned empty output")

    _reject_unsafe_wrappers(revised, original_text)
    if _looks_like_commentary(revised, original_text):
        _reject("commentary", "Local model returned commentary")
    if _looks_like_prompt_echo(revised, original_text):
        _reject("prompt_echo", "Local model echoed prompt or system text")

    if original_text is not None:
        revised = _preserve_source_typography(revised, original_text)
        revised = _restore_source_line_endings(revised, original_text)
        _validate_list_markers(revised, original_text)
        _validate_reasonable_completeness(revised, original_text)
    _validate_revised_text(revised, original_text=original_text)
    return revised


def _reject(reason: str, message: str) -> None:
    raise OfflineWritingMalformedOutput(message, reason=reason)


def _normalized(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _is_single_wrapped_quote(value: str) -> bool:
    return bool(re.match(r'''^(["']).*\1$''', value, flags=re.DOTALL))


def _reject_unsafe_wrappers(revised: str, original_text: str | None) -> None:
    original = _normalized(original_text or "")
    if (
        MARKDOWN_HEADING_PATTERN.search(revised)
        or MARKDOWN_LABEL_PATTERN.match(revised)
        or MARKDOWN_EMPHASIS_WRAPPER_PATTERN.match(revised)
    ) and not (
        MARKDOWN_HEADING_PATTERN.search(original)
        or MARKDOWN_LABEL_PATTERN.match(original)
        or MARKDOWN_EMPHASIS_WRAPPER_PATTERN.match(original)
    ):
        _reject("markdown_wrapper", "Local model returned a Markdown wrapper")
    stripped = revised.strip()
    original_stripped = original.strip()
    json_wrapped = (
        stripped.startswith("{") and stripped.endswith("}")
    ) or (
        stripped.startswith("[") and stripped.endswith("]")
    )
    original_json = (
        original_stripped.startswith("{") and original_stripped.endswith("}")
    ) or (
        original_stripped.startswith("[") and original_stripped.endswith("]")
    )
    if json_wrapped and not original_json:
        _reject("json_wrapper", "Local model returned a JSON wrapper")
    if XML_WRAPPER_PATTERN.match(stripped) and not XML_WRAPPER_PATTERN.match(
        original_stripped
    ):
        _reject("xml_wrapper", "Local model returned an XML wrapper")


def _looks_like_commentary(revised: str, original_text: str | None) -> bool:
    lowered = revised.lstrip().casefold()
    original_lowered = (
        original_text.lstrip().casefold() if original_text is not None else ""
    )
    return any(
        lowered.startswith(prefix) and not original_lowered.startswith(prefix)
        for prefix in COMMENTARY_PREFIXES
    )


def _looks_like_prompt_echo(revised: str, original_text: str | None) -> bool:
    lowered = revised.casefold()
    original_lowered = (original_text or "").casefold()
    return any(
        phrase in lowered and phrase not in original_lowered
        for phrase in PROMPT_ECHO_PHRASES
    )


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
    return preserved


def _restore_source_line_endings(revised: str, original_text: str) -> str:
    if "\r\n" in original_text and "\n" not in original_text.replace("\r\n", ""):
        return revised.replace("\n", "\r\n")
    return revised


def _validate_list_markers(revised: str, original_text: str) -> None:
    original_markers = LIST_MARKER_PATTERN.findall(_normalized(original_text))
    if not original_markers:
        return
    revised_markers = LIST_MARKER_PATTERN.findall(_normalized(revised))
    if original_markers != revised_markers:
        _reject("list_structure_changed", "Local model changed list structure")


def _validate_reasonable_completeness(revised: str, original_text: str) -> None:
    original_length = len(original_text.strip())
    revised_length = len(revised.strip())
    if original_length >= 80 and revised_length < original_length * 0.35:
        _reject("obvious_truncation", "Local model output was obviously truncated")


def _validate_revised_text(revised: str, original_text: str | None = None) -> None:
    if not revised.strip():
        _reject("empty_output", "Local model returned empty output")
    if "\x00" in revised or "\x1b" in revised or CONTROL_CHARACTER_PATTERN.search(
        revised
    ):
        _reject("control_characters", "Local model returned control characters")
    try:
        revised.encode("utf-8")
    except UnicodeError as exc:
        raise OfflineWritingMalformedOutput(
            "Local model returned invalid Unicode", reason="invalid_unicode"
        ) from exc
    if any(unicodedata.category(character) == "Cs" for character in revised):
        _reject("invalid_unicode", "Local model returned invalid Unicode")
    if original_text is not None:
        original_len = max(len(original_text.strip()), 1)
        max_len = max(original_len * 3, original_len + 100)
        if len(revised) > max_len:
            _reject("excessive_expansion", "Local model output was suspiciously long")
