from __future__ import annotations


REVISION_INSTRUCTION = """Make the supplied text correct, natural, clear, and professional while preserving the author's intended meaning, factual content, and overall tone.

Correct spelling, grammar, punctuation, awkward or non-native phrasing, poor word choice, and unnecessary redundancy. Restructure a sentence only when that makes it clearly better. Do not rewrite correct, clear, natural text merely for variety.

Preserve all facts and meaning-bearing details, especially names, numbers, dates, times, quantities, currencies, URLs, email addresses, technical identifiers, quoted text, negation, modality, questions, and the author's intent. Keep every number attached to the same noun or label; never add a unit such as "times" or change what a number identifies.

Preserve every line break, blank line, paragraph boundary, indentation, and list marker. Do not add or remove content.

If the text is already correct, clear, and natural, return it exactly unchanged.

Return only the revised text. Never add explanations, headings, commentary, markdown fences, analysis, or reasoning."""
