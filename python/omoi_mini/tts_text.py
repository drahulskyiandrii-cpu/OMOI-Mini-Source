"""Konservative RU-TTS-Textvorbereitung, keine automatische Betonungswahl.

Experimenteller Baustein; nicht automatisch in Produktiv-TTS aktiviert.
"""
from __future__ import annotations
import re
from collections.abc import Mapping

RU_IT_TERMS = {
    "DNS": "дэ эн эс", "VPN": "ви пи эн", "CPU": "си пи ю",
    "GPU": "джи пи ю", "SSD": "эс эс ди", "HDD": "эйч ди ди",
    "RAM": "оперативная память", "WI-FI": "вай фай",
    "IPV4": "ай пи версии четыре", "IPV6": "ай пи версии шесть",
}
_TERM = re.compile(
    r"(?<!\w)(?:IPv[46]|Wi[-‑ ]?Fi|DNS|VPN|CPU|GPU|SSD|HDD|RAM)(?!\w)",
    re.IGNORECASE,
)
_AMBIGUOUS = re.compile(
    r"(?<!\w)(?:замок|мука|плачу|атлас|ирис|все)(?!\w)", re.IGNORECASE
)
_MARKDOWN_LINK = re.compile(r"\[([^\]\r\n]{1,1000})\]\(https?://[^\s)]+\)")
_BARE_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
_HEADER = re.compile(r"(?m)^\s*#{1,6}\s+")
_CODE_BLOCK = re.compile(r"(?s)\x60{3}.*?\x60{3}")


def prepare_russian_speech(
    text: str,
    *,
    expand_it_terms: bool = False,
    pronunciations: Mapping[str, str] | None = None,
) -> str:
    """Beseitigt Markup; Fachkürzel/Pronunciation nur nach explizitem Opt-in."""
    if not isinstance(text, str):
        raise TypeError("SPEECH_TEXT_MUST_BE_STRING")
    if not 0 < len(text) <= 16000:
        raise ValueError("INVALID_SPEECH_TEXT_LENGTH")

    value = text.replace("\u00a0", " ").replace("\u202f", " ")
    value = _CODE_BLOCK.sub(" ", value)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = _BARE_URL.sub("ссылка", value)
    value = _HEADER.sub("", value)
    value = _LIST_ITEM.sub(". ", value)
    value = re.sub(r"[\x60*_]{1,3}", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([.,!?;:])", r"\1", value)
    value = re.sub(r"\.{2,}", ".", value)
    value = value.lstrip(". ")
    if not value:
        raise ValueError("NO_SPEAKABLE_TEXT")

    if expand_it_terms:
        def to_spoken(match: re.Match[str]) -> str:
            canonical = match.group().replace("‑", "-").replace(" ", "-").upper()
            return RU_IT_TERMS[canonical]
        value = _TERM.sub(to_spoken, value)

    if pronunciations:
        replacements = {}
        for key, replacement in pronunciations.items():
            if not key or len(key) > 120 or not replacement or len(replacement) > 250:
                raise ValueError("INVALID_MANUAL_PRONUNCIATION")
            if any(char in key + replacement for char in "\n\r<>"):
                raise ValueError("INVALID_MANUAL_PRONUNCIATION")
            replacements[key.casefold()] = replacement
        if replacements:
            pattern = re.compile(
                r"(?<!\w)(?:" + "|".join(
                    re.escape(key) for key in sorted(replacements, key=len, reverse=True)
                ) + r")(?!\w)", re.IGNORECASE,
            )
            value = pattern.sub(lambda m: replacements[m.group().casefold()], value)
    return value


def flag_ambiguous_russian_words(text: str) -> tuple[str, ...]:
    """Nur Warnung bei potenziellen Homographen, keine automatische Ersetzung."""
    return tuple(dict.fromkeys(match.group().casefold() for match in _AMBIGUOUS.finditer(text)))


def split_for_tts(text: str, *, max_chars: int = 240) -> list[str]:
    """Teilt an Satzgrenzen, ohne Wörter oder Satzzeichen zu verlieren."""
    if max_chars < 60:
        raise ValueError("MAX_CHARS_TOO_SMALL")
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        proposal = (current + " " + sentence).strip()
        if current and len(proposal) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = proposal
    if current:
        chunks.append(current)
    return chunks
