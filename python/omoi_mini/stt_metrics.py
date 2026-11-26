"""Reine Textmetriken: keine Modelle, Audio-Dateien oder Netzwerkzugriffe."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass,field


def words(text:str)->list[str]:
    """NFC, Kleinschreibung, Satzzeichen ignorieren; Umlaute und ss/Eszett erhalten."""
    if not isinstance(text,str) or len(text)>4000:
        raise ValueError("Ungueltiger Benchmark-Text")
    return re.findall(r"[^\W_]+",unicodedata.normalize("NFC",text).lower())


def edit_distance(a,b)->int:
    """Levenshtein: Einfuegung, Loeschung, Ersetzung kosten jeweils eins."""
    if len(b)>len(a):a,b=b,a
    previous=list(range(len(b)+1))
    for i,token in enumerate(a,1):
        row=[i]
        for j,other in enumerate(b,1):
            row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(token!=other)))
        previous=row
    return previous[-1]


@dataclass
class ErrorTotals:
    cases:int=0
    words:int=0
    word_errors:int=0
    chars:int=0
    char_errors:int=0
    silence_cases:int=0
    silence_hallucinations:int=0

    def add(self,reference,hypothesis):
        a,b=words(reference),words(hypothesis)
        self.cases+=1
        if not a:
            # WER/CER fuer Stille sind nicht definiert.
            self.silence_cases+=1
            self.silence_hallucinations+=int(bool(b))
            return
        ac,bc="".join(a),"".join(b)
        self.words+=len(a)
        self.chars+=len(ac)
        self.word_errors+=edit_distance(a,b)
        self.char_errors+=edit_distance(ac,bc)

    def report(self):
        return dict(cases=self.cases,reference_words=self.words,
                    word_errors=self.word_errors,wer=self.word_errors/self.words if self.words else None,
                    reference_chars=self.chars,char_errors=self.char_errors,
                    cer=self.char_errors/self.chars if self.chars else None,
                    silence_cases=self.silence_cases,silence_hallucinations=self.silence_hallucinations)


def percentile(values,fraction):
    if not values:return None
    return sorted(values)[max(0,math.ceil(len(values)*fraction)-1)]


@dataclass
class BenchmarkGroup:
    raw:ErrorTotals=field(default_factory=ErrorTotals)
    corrected:ErrorTotals=field(default_factory=ErrorTotals)
    paired_raw:ErrorTotals=field(default_factory=ErrorTotals)
    latency:list[float]=field(default_factory=list)
    audio_ms:float=0
    inference_ms:float=0
    failed:int=0
    seen:set[str]=field(default_factory=set)

    def add(self,row):
        case=row["case"]
        if not isinstance(case,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",case) or case in self.seen:
            raise ValueError("Doppelte oder ungueltige Fall-ID")
        self.seen.add(case)
        reference=row["reference"]
        words(reference)
        ok=row.get("ok",True)
        if type(ok) is not bool:raise ValueError("Boolean erwartet")
        if not ok:
            self.failed+=1
            # Ein Fehler ist eine leere Hypothese, nicht ein ausgelassener Test.
        hypothesis=row["hypothesis"] if ok else ""
        self.raw.add(reference,hypothesis)
        if ok and "corrected" in row:
            self.paired_raw.add(reference,hypothesis)
            self.corrected.add(reference,row["corrected"])
        for key in ("latency_ms","audio_ms"):
            value=row.get(key)
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):
                raise ValueError("Ungueltige Zeitmessung")
        latency=row.get("latency_ms")
        duration=row.get("audio_ms")
        if ok and latency is not None:
            self.latency.append(latency)
            if duration is not None and duration>0:
                self.audio_ms+=duration
                self.inference_ms+=latency

    def report(self):
        return dict(raw=self.raw.report(),corrected=self.corrected.report(),
                    paired_raw=self.paired_raw.report(),failed=self.failed,
                    latency_samples=len(self.latency),p50_ms=percentile(self.latency,.5),
                    p95_ms=percentile(self.latency,.95),
                    rtf=self.inference_ms/self.audio_ms if self.audio_ms else None)


def evaluate(rows):
    groups={}
    for row in rows:
        engine=row["engine"]
        language=row["language"]
        if not isinstance(engine,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",engine):
            raise ValueError("Ungueltiger Engine-Name")
        if language not in ("de","ru","en"):raise ValueError("Ungueltige Sprache")
        groups.setdefault((engine,language),BenchmarkGroup()).add(row)
    return [{"engine":e,"language":lang,**group.report()} for (e,lang),group in sorted(groups.items())]
