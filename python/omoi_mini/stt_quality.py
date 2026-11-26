"""CPU-only STT quality policies ported from OMOI 3cdfc429."""
from __future__ import annotations
import re
import unicodedata
from .stt_domain_rules import RULES

PROFILES = ("fast", "balanced", "quality")
BASE_TERMS = ("OMOI", "OMOI Mini", "Whisper", "Faster-Whisper", "OpenVINO", "CUDA",
              "Qwen", "Gemma", "GPU", "CPU", "NPU", "STT", "TTS")


def hotwords(custom: str = "") -> str:
    terms = []
    # Benutzerbegriffe haben Vorrang vor den allgemeinen IT-Begriffen.
    for term in (*re.split(r"[,;\n]", custom), *BASE_TERMS):
        term = unicodedata.normalize("NFKC", term)
        term = " ".join("".join(" " if unicodedata.category(c).startswith("C") else c
                               for c in term).split())[:80]
        if term and term.casefold() not in {t.casefold() for t in terms}:
            if len(", ".join((*terms, term)).encode("utf-8")) <= 192:
                terms.append(term)
    return ", ".join(terms)


def _regex(pair):
    pattern, flags = pair
    # JS Unicode property class L/N/_ maps to Python Unicode word chars.
    pattern = pattern.replace(r"[\p{L}\p{N}_]", r"\w")
    return re.compile(pattern, re.I if "i" in flags else 0)


_COMPILED = tuple((r, _regex(r["pattern"]),
                   tuple(map(_regex, r["contextAny"])),
                   tuple(map(_regex, r["contextAll"]))) for r in RULES)


def correct_domain(text: str, language: str | None) -> tuple[str, tuple[str, ...]]:
    changed = []
    for rule, pattern, any_context, all_context in _COMPILED:
        if rule["confidence"] < .95 or (language and language != "auto" and
                rule["languages"] and language not in rule["languages"]):
            continue
        if any_context and not any(p.search(text) for p in any_context):
            continue
        if not all(p.search(text) for p in all_context):
            continue
        text, count = pattern.subn(lambda _: rule["replacement"], text)
        if count:
            changed.append(rule["id"])
    return text, tuple(changed)


def normalize_language(value: str | None) -> str | None:
    if not isinstance(value,str):return None
    value = value.strip().lower().removeprefix("<|").removesuffix("|>")
    value = re.split(r"[-_]", value)[0]
    return {"rus":"ru", "russian":"ru", "deu":"de", "ger":"de", "german":"de",
            "eng":"en", "english":"en"}.get(value, value) or None


def auto_language_issue(detected: str | None, text: str) -> str | None:
    language = normalize_language(detected)
    if language and language not in ("ru", "de", "en"):
        return "unsupported-detected-language"
    if len(re.findall(r"[àáâãåæçèéêëìíîïñòóôõøœùúûýÿ]", text, re.I)) >= 2:
        return "unsupported-latin-orthography"
    return None


class PartialStabilizer:
    """Two consecutive matching word prefixes; final transcript is authoritative."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = []
        self.emitted = []
        self.stable = ""

    def update(self, text: str) -> tuple[str, str]:
        if not text:
            return self.stable, ""
        # Include combining marks and trailing punctuation in displayed spans.
        spans = []
        start = None
        for i, c in enumerate(text + " "):
            word = unicodedata.category(c)[0] in "LMN"
            if word and start is None:
                start = i
            elif not word and start is not None:
                spans.append((start, i))
                start = None
        words = [unicodedata.normalize("NFC", text[a:b]).lower() for a,b in spans]
        # If a later hypothesis contradicts committed words, show the fresh
        # hypothesis as provisional instead of concatenating incompatible text.
        if words[:len(self.emitted)] != self.emitted:
            self.reset()
        n = len(self.emitted)
        while n < min(len(words), len(self.previous)) and words[n] == self.previous[n]:
            n += 1
        self.emitted = words[:n]
        self.previous = words
        if not self.emitted:
            self.stable=""
            return "", text
        end = spans[n-1][1]
        next_start = spans[n][0] if n<len(spans) else len(text)
        while end < next_start and not text[end].isspace():
            end += 1
        # Stabile Woerter behalten, Schreibweise/Satzzeichen der aktuellen
        # Hypothese verwenden. Stable + Tail muss exakt Text ergeben.
        self.stable = text[:end]
        return self.stable, text[end:]



def decoder_options(profile: str, *, partial: bool, custom: str = "") -> dict:
    if profile not in PROFILES:
        raise ValueError("Unknown STT profile")
    if profile == "fast":
        return {}
    return {"beam_size": 5 if profile == "quality" and not partial else 1,
            "hotwords": hotwords(custom) if custom.strip() else "", "vad_filter": False}
