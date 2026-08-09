from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable


URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w-])"
)
PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})"
    r"(?:[\s.-]?\d{2,4}){2,4}(?!\w)"
)
NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:[$£€¥₹]\s*)?[+-]?\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s?(?:%|USD|CAD|EUR|GBP|JPY|AUD))?(?![\w])",
    re.IGNORECASE,
)
HASHED_NUMBER_PATTERN = re.compile(
    r"(?<![\w])#\s*[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\w])"
)
MONTH_DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)
NUMERIC_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?!\d)"
)
CALENDAR_PATTERN = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(
    r"(?<!\w)\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?(?!\w)|"
    r"(?<!\w)\d{1,2}\s?[ap]\.?m\.?(?!\w)",
    re.IGNORECASE,
)
IDENTIFIER_PATTERN = re.compile(
    r"\b(?=[A-Za-z0-9_.:/-]*[A-Za-z])(?=[A-Za-z0-9_.:/-]*\d)"
    r"[A-Za-z][A-Za-z0-9]*(?:[_.:/-][A-Za-z0-9]+)+\b"
    r"|\b[A-Z]{2,}[A-Z0-9_.:/-]*\b"
)
CAPITALIZED_NAME_PATTERN = re.compile(
    r"\b[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|&)){1,4}\b"
)
SUBJECT_NAME_PATTERN = re.compile(
    r"\b(?!(?:The|This|That|These|Those|They|He|She|It|We|You|Please|"
    r"There|Here)\b)"
    r"[A-Z][a-z]{2,}\b(?=\s+(?:may|might|must|should|can|could|"
    r"will|would|is|was|has|had|approved|announced|reported|said|"
    r"requested|promised|agreed|declined|refused|denied)\b)"
)
QUOTED_PATTERN = re.compile(
    r'"[^"\r\n]+"|“[^”\r\n]+”|‘[^’\r\n]+’', re.UNICODE
)
NEGATION_PATTERN = re.compile(
    r"\b(?:not|never|neither|nor|no|cannot|"
    r"(?:can|won|wouldn|shouldn|couldn|mustn|isn|aren|wasn|weren|"
    r"don|doesn|didn|hasn|haven|hadn)['’]?t)\b",
    re.IGNORECASE,
)
LEXICAL_NEGATION_PATTERN = re.compile(
    r"\b(?:unable|unavailable|impossible|unlikely|unwell|without)\b",
    re.IGNORECASE,
)
MODAL_PATTERN = re.compile(
    r"\b(?:may|might|must|should|can|cannot|can't|cant|could|"
    r"will|won't|wont|would|shall|ought)\b",
    re.IGNORECASE,
)
CERTAINTY_PATTERN = re.compile(
    r"\b(?:certainly|definitely|probably|possibly|perhaps|"
    r"likely|unlikely|apparently|seemingly|appears?|seems?|doubt|doubtful)\b",
    re.IGNORECASE,
)
CAUSAL_TEMPORAL_PATTERN = re.compile(
    r"\b(?:because|due\s+to|as(?=\s+(?:I|we|you|he|she|they|it)\b)|"
    r"therefore|thus|hence|consequently|before|after|"
    r"then|until|unless|while|during|since|when|whenever)\b",
    re.IGNORECASE,
)
INTENT_PATTERN = re.compile(
    r"\b(?:promis(?:e|es|ed|ing)|commit(?:s|ted|ting)?|"
    r"agree(?:s|d|ing)?|refus(?:e|es|ed|ing)|declin(?:e|es|ed|ing)|"
    r"approv(?:e|es|ed|ing)|den(?:y|ies|ied|ying)|"
    r"decid(?:e|es|ed|ing)|decision)\b",
    re.IGNORECASE,
)
POLITENESS_PATTERN = re.compile(r"\b(?:please|kindly)\b", re.IGNORECASE)
QUESTION_PATTERN = re.compile(r"\?(?=(?:[\"'”’)\]]*)\s*(?:$|\n))")
REFERENCE_PATTERN = re.compile(
    r"\b(?P<determiner>the|a|an|this|that|these|those)\s+"
    r"(?P<noun>[A-Za-z][A-Za-z'-]*)\b",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
ANCHOR_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
    "by", "do", "does", "did", "for", "from", "had", "has", "have", "he",
    "her", "him", "his", "i", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "our", "she", "that", "the", "their", "them", "they",
    "this", "to", "us", "was", "we", "were", "with", "you", "your",
}
CASING_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


@dataclass(frozen=True)
class SemanticValidation:
    accepted: bool
    reasons: tuple[str, ...]
    protected_source: dict[str, Counter[str]]
    protected_candidate: dict[str, Counter[str]]


def validate_semantic_preservation(
    source: str, candidate: str
) -> SemanticValidation:
    """Apply deterministic high-precision guards against obvious meaning drift.

    This is deliberately a safety gate, not a mathematical proof of semantic
    equivalence. It protects facts and meaning-bearing operators that are
    especially risky for a small local language model to change.
    """

    source_values = protected_values(source)
    candidate_values = protected_values(candidate)
    reasons: list[str] = []
    for category in (
        "urls",
        "emails",
        "phones",
        "numbers",
        "dates",
        "times",
        "identifiers",
        "quotes",
        "names",
        "negation",
        "modality",
        "certainty",
        "relations",
        "intent",
        "politeness",
    ):
        if source_values[category] != candidate_values[category]:
            reasons.append(f"{category}_not_preserved")

    if source_values["numbers"] and _number_contexts(source) != _number_contexts(
        candidate
    ):
        reasons.append("number_context_changed")

    if len(QUESTION_PATTERN.findall(source)) != len(
        QUESTION_PATTERN.findall(candidate)
    ):
        reasons.append("question_structure_changed")
    if not _shared_references_preserved(source, candidate):
        reasons.append("reference_not_preserved")

    return SemanticValidation(
        accepted=not reasons,
        reasons=tuple(reasons),
        protected_source=source_values,
        protected_candidate=candidate_values,
    )


def protected_values(value: str) -> dict[str, Counter[str]]:
    return {
        "urls": _matches(URL_PATTERN, value),
        "emails": _matches(EMAIL_PATTERN, value),
        "phones": _matches(PHONE_PATTERN, value),
        "numbers": _combined_matches(
            value, NUMBER_PATTERN, HASHED_NUMBER_PATTERN
        ),
        "dates": _combined_matches(
            value, MONTH_DATE_PATTERN, NUMERIC_DATE_PATTERN, CALENDAR_PATTERN
        ),
        "times": _matches(TIME_PATTERN, value),
        "identifiers": _matches(IDENTIFIER_PATTERN, value),
        "quotes": _matches(QUOTED_PATTERN, value, casefold=False),
        "names": _combined_matches(
            value, CAPITALIZED_NAME_PATTERN, SUBJECT_NAME_PATTERN
        ),
        "negation": Counter(_canonical_negations(value)),
        "modality": Counter(_canonical_modality(value)),
        "certainty": _matches(CERTAINTY_PATTERN, value),
        "relations": Counter(_canonical_relations(value)),
        "intent": Counter(_canonical_intent(value)),
        "politeness": Counter(
            "polite" for _match in POLITENESS_PATTERN.finditer(value)
        ),
    }


def restore_source_word_casing(source: str, candidate: str) -> str:
    """Preserve casing for uniquely matched words outside sentence starts."""
    source_matches: dict[str, list[str]] = {}
    candidate_matches: dict[str, list[re.Match[str]]] = {}
    for match in CASING_WORD_PATTERN.finditer(source):
        source_matches.setdefault(match.group(0).casefold(), []).append(
            match.group(0)
        )
    for match in CASING_WORD_PATTERN.finditer(candidate):
        candidate_matches.setdefault(match.group(0).casefold(), []).append(
            match
        )

    replacements: list[tuple[int, int, str]] = []
    for folded, source_words in source_matches.items():
        matches = candidate_matches.get(folded, [])
        if len(source_words) != 1 or len(matches) != 1:
            continue
        match = matches[0]
        source_word = source_words[0]
        if source_word == match.group(0):
            continue
        prefix = candidate[: match.start()].rstrip()
        if not prefix or prefix[-1] in ".!?\r\n":
            continue
        replacements.append((match.start(), match.end(), source_word))

    restored = candidate
    for start, end, source_word in reversed(replacements):
        restored = restored[:start] + source_word + restored[end:]
    return restored


def restore_source_number_formatting(source: str, candidate: str) -> str:
    """Remove a newly added number-sign prefix from a unique source number."""
    source_numbers = _matches(NUMBER_PATTERN, source, casefold=False)
    hashed_matches = list(HASHED_NUMBER_PATTERN.finditer(candidate))
    replacements: list[tuple[int, int, str]] = []
    for match in hashed_matches:
        number = match.group(0).removeprefix("#").lstrip()
        if source_numbers[number] == 1:
            replacements.append((match.start(), match.end(), number))

    restored = candidate
    for start, end, number in reversed(replacements):
        restored = restored[:start] + number + restored[end:]
    return restored


def meaning_anchor_preserved(source: str, candidate: str) -> bool:
    """Reject unrelated output or severe deletion without enforcing locality.

    Anchors are compared across the complete selection so sentence combining,
    splitting, reordering, and substantial paraphrasing remain possible.
    """
    source_anchors = Counter(_anchors(source))
    if len(source_anchors) < 4:
        return True
    candidate_anchors = Counter(_anchors(candidate))
    overlap = sum(
        min(count, candidate_anchors[token])
        for token, count in source_anchors.items()
    )
    overlap_ratio = overlap / sum(source_anchors.values())
    if overlap_ratio < 0.20:
        return False
    source_length = max(len(source.strip()), 1)
    if len(candidate.strip()) < source_length * 0.60 and overlap_ratio < 0.70:
        return False
    return True


def _matches(
    pattern: re.Pattern[str], value: str, *, casefold: bool = True
) -> Counter[str]:
    found = pattern.findall(value)
    return Counter(item.casefold() if casefold else item for item in found)


def _number_contexts(value: str) -> Counter[tuple[str, str, str]]:
    """Protect the grammatical role of a number, not only its literal value."""
    words = list(WORD_PATTERN.finditer(value))
    contexts: Counter[tuple[str, str, str]] = Counter()
    for number in NUMBER_PATTERN.finditer(value):
        previous = _nearest_anchor(
            word.group(0)
            for word in reversed(words)
            if word.end() <= number.start()
        )
        following = _nearest_anchor(
            word.group(0) for word in words if word.start() >= number.end()
        )
        contexts[(number.group(0).casefold(), previous, following)] += 1
    return contexts


def _nearest_anchor(words: Iterable[str]) -> str:
    for word in words:
        token = word.casefold().replace("â€™", "'")
        if token in ANCHOR_STOPWORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        return token
    return ""


def _combined_matches(
    value: str, *patterns: re.Pattern[str]
) -> Counter[str]:
    items: list[str] = []
    for pattern in patterns:
        items.extend(match.group(0).casefold() for match in pattern.finditer(value))
    return Counter(items)


def _canonical_negations(value: str) -> Iterable[str]:
    for match in NEGATION_PATTERN.finditer(value):
        token = match.group(0).casefold().replace("’", "'")
        if token in {"cannot", "can't", "cant"}:
            yield "not"
        elif token in {"never", "neither", "nor", "no"}:
            yield token
        else:
            yield "not"
    for _match in LEXICAL_NEGATION_PATTERN.finditer(value):
        yield "not"


def _canonical_modality(value: str) -> Iterable[str]:
    for match in MODAL_PATTERN.finditer(value):
        token = match.group(0).casefold().replace("’", "'")
        if token in {"cannot", "can't", "cant"}:
            yield "can"
        elif token in {"won't", "wont"}:
            yield "will"
        else:
            yield token


def _canonical_intent(value: str) -> Iterable[str]:
    for match in INTENT_PATTERN.finditer(value):
        token = match.group(0).casefold()
        if token.startswith("promis"):
            yield "promise"
        elif token.startswith("commit"):
            yield "commit"
        elif token.startswith("agree"):
            yield "agree"
        elif token.startswith("refus"):
            yield "refuse"
        elif token.startswith("declin"):
            yield "decline"
        elif token.startswith("approv"):
            yield "approve"
        elif token.startswith("den"):
            yield "deny"
        else:
            yield "decide"


def _canonical_relations(value: str) -> Iterable[str]:
    for match in CAUSAL_TEMPORAL_PATTERN.finditer(value):
        token = match.group(0).casefold()
        if token in {"because", "due to", "as"}:
            yield "cause"
        else:
            yield "after" if token == "then" else token


def _shared_references_preserved(source: str, candidate: str) -> bool:
    source_refs = _references(source)
    candidate_refs = _references(candidate)
    return all(
        candidate_refs[noun] == determiners
        for noun, determiners in source_refs.items()
        if noun in candidate_refs
    )


def _references(value: str) -> dict[str, Counter[str]]:
    references: dict[str, Counter[str]] = {}
    for match in REFERENCE_PATTERN.finditer(value):
        noun = match.group("noun").casefold()
        if noun in {"is", "are", "was", "were", "will", "would", "can", "may"}:
            continue
        determiner = match.group("determiner").casefold()
        category = (
            "definite"
            if determiner == "the"
            else "indefinite"
            if determiner in {"a", "an"}
            else determiner
        )
        references.setdefault(noun, Counter())[category] += 1
    return references


def _anchors(value: str) -> list[str]:
    anchors: list[str] = []
    for raw in WORD_PATTERN.findall(value):
        token = raw.casefold().replace("’", "'")
        if token in ANCHOR_STOPWORDS:
            continue
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("es") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        anchors.append(token)
    return anchors
