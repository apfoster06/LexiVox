"""Small text helpers for dictation: filler-word removal and vocabulary prompts."""

from __future__ import annotations

import re

# Hyphenated words like "mm-hmm" and "uh-huh" mean "yes", so they're kept: a filler
# must not touch a hyphen or another word character.
FILLERS = r"(?<![-\w])(?:u+m+|u+h+|e+r+m+|er|a+h+|h+m+|m+h*m+)(?![-\w])"
# A filler at the start of the text or of a sentence, plus any commas after it.
_LEADING = re.compile(rf"(^|[.!?]\s+)(?:{FILLERS}[,.…]*\s*)+(\S)", re.IGNORECASE)
# A filler anywhere else, with the commas around it.
_INNER = re.compile(rf"(,?)\s*{FILLERS}(\s*,)?", re.IGNORECASE)


def _inner(m: re.Match) -> str:
    before, after = m.group(1), m.group(2)
    if before and after:  # "I think, um, we" -> "I think we"
        return ""
    return "," if before or after else ""
_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")


def remove_fillers(text: str) -> str:
    """Drop "um", "uh", "erm" and similar, and tidy the punctuation left behind."""
    text = _LEADING.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    text = _INNER.sub(_inner, text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = re.sub(r",(?=\S)(?![\d])", ", ", text)  # "Okay,let" -> "Okay, let"
    text = re.sub(r",([.!?])", r"\1", text)  # "send it,." -> "send it."
    text = _SPACES.sub(" ", text).strip()
    return re.sub(r"^[,.;:]\s*", "", text)


def vocabulary_terms(raw: str, limit: int = 60) -> list[str]:
    """Split a user's vocabulary list (one per line or comma separated)."""
    terms, seen = [], set()
    for part in re.split(r"[\n,;]+", raw):
        term = part.strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term[:60])
        if len(terms) >= limit:
            break
    return terms


def vocabulary_prompt(raw: str, max_chars: int = 600) -> str | None:
    """Whisper uses the initial prompt as spelling context; keep it short."""
    terms = vocabulary_terms(raw)
    if not terms:
        return None
    prompt = "Glossary: " + ", ".join(terms) + "."
    return prompt[:max_chars]
