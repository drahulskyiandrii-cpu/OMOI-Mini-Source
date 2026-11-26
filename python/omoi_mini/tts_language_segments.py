"""Experimentelles RU/DE-Routing ohne Modell- oder Provider-Aufruf.

Nur eindeutige Textabschnitte trennen; kein automatischer Produktionswechsel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-zÄÖÜäöüß]+")
_GERMAN = frozenset(
    "ich du er sie es wir ihr ihnen uns mir dir mich dich mein meine dein deine "
    "der die das den dem des ein eine einen einem einer und oder aber nicht kein "
    "keine ist sind bin bist war waren sein haben hat hast wird werden kann "
    "kannst können bitte danke hallo guten morgen tag abend wie was warum "
    "welche welcher welches heute jetzt hier dort mit für von zu zum zur im "
    "auf aus bei über unter nach vor durch auch noch sehr so geht gut "
    "möchte soll muss müssen spreche sprechen deutsch verstehe erkläre".split()
)
# Satz- und explizite Klauselgrenzen, inklusive originaler Leerzeichen.
_BOUNDARY = re.compile(r"(?<=[.!?…;:,])(?:[ \t]+|\n+)|\n+|\s+[–—]\s+")


@dataclass(frozen=True)
class SpeechSegment:
    language: str
    text: str


def _language(text: str, fallback: str) -> str:
    if _CYR.search(text):
        # Lateinische Fachwörter in russischer Klausel nicht umschalten.
        return "ru"
    words = [word.casefold() for word in _LAT.findall(text)]
    german = sum(word in _GERMAN for word in words)
    if german >= 2 or (german and re.search(r"[äöüßÄÖÜ]", text)):
        return "de"
    return fallback


def plan_ru_de(text: str, *, fallback: str) -> tuple[SpeechSegment, ...]:
    """Verlustfreie, konservative Klauselplanung; unklare Abschnitte erben Sprache.

    Kein Sprachdetektor für beliebige Sprachen, keine Übersetzung, kein Textumbau.
    Fallback muss aus der bewussten App-Sprachwahl stammen.
    """
    if fallback not in {"ru", "de"}:
        raise ValueError("INVALID_SPEECH_LANGUAGE")
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError("INVALID_SPEECH_TEXT")
    if not text.strip():
        return ()
    segments: list[SpeechSegment] = []
    start = 0
    for boundary in (*_BOUNDARY.finditer(text), None):
        end = boundary.end() if boundary is not None else len(text)
        part = text[start:end]
        start = end
        if not part:
            continue
        previous = segments[-1].language if segments else fallback
        language = _language(part, previous)
        if segments and segments[-1].language == language:
            segments[-1] = SpeechSegment(language, segments[-1].text + part)
        else:
            segments.append(SpeechSegment(language, part))
    return tuple(segments)
