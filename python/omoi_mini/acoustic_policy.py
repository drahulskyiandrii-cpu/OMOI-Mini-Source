"""Offline-Qualifikation akustischer Evidenz, ohne Modellimport.

Silero-Schwellen aus Haupt-OMOI f8e414ce. ECAPA fuer DE/RU/EN
konservativ angepasst. Keine automatische Routing- oder Veto-Entscheidung.
"""
from dataclasses import dataclass
import math


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


@dataclass(frozen=True)
class VadEvidence:
    lane:str|None
    active_frames:int
    longest_run:int
    max_probability:float
    active_mean:float


def silero_evidence(probabilities):
    """512 Samples/32 ms pro Frame; hoechstens 30 s plus Padding."""
    if not isinstance(probabilities,(list,tuple)) or not 1<=len(probabilities)<=938:
        raise ValueError("Invalid Silero frame count")
    active=run=longest=0
    total=peak=0.
    for p in probabilities:
        if not _finite(p) or not 0<=p<=1:
            raise ValueError("Invalid Silero probability")
        if p>=.5:
            active+=1
            run+=1
            longest=max(longest,run)
            total+=p
        else:run=0
        peak=max(peak,p)
    mean=total/active if active else 0.
    lane=None
    if active>=8 and longest>=8:lane="sustained"
    elif 4<=active<=10 and longest>=4 and peak>=.88 and mean>=.72:
        lane="short-high-confidence"
    return VadEvidence(lane,active,longest,peak,mean)


LANGUAGE_CLASSES={22:"ru",24:"en",44:"de"}


@dataclass(frozen=True)
class LanguageEvidence:
    language:str|None
    reason:str
    confidence:float
    margin:float


def ecapa_evidence(logits):
    """Globale 45-Klassen-Verteilung, keine erzwungene RU/DE-Zuordnung.

    Vorlaeufige, bewusst konservative Grenzen: erst mit echter
    DE/RU/EN-Validierung kalibrieren. Scores sind nicht kalibriert.
    """
    if not isinstance(logits,(list,tuple)) or len(logits)!=45 or not all(map(_finite,logits)):
        raise ValueError("Invalid ECAPA logits")
    peak=max(logits)
    scores=[math.exp(x-peak) for x in logits]
    total=sum(scores)
    order=sorted(range(45),key=lambda i:scores[i],reverse=True)
    best,second=order[:2]
    confidence=scores[best]/total
    margin=(scores[best]-scores[second])/total
    language=LANGUAGE_CLASSES.get(best)
    if language is None:return LanguageEvidence(None,"out-of-profile",confidence,margin)
    if confidence<.85 or margin<.20:return LanguageEvidence(None,"uncertain",confidence,margin)
    return LanguageEvidence(language,"supported",confidence,margin)


@dataclass(frozen=True)
class AudioEventEvidence:
    event:str|None
    speech_score:float
    cough_score:float
    throat_clearing_score:float
    sneeze_score:float
    frames:int


def yamnet_evidence(scores):
    """Schwellen aus Haupt-OMOI; nur Evidenz, kein automatisches Veto."""
    if not isinstance(scores,dict):raise ValueError("Invalid YAMNet scores")
    names=("speech","cough","throatClearing","sneeze")
    values=[scores[k] for k in names]
    frames=scores["frames"]
    if not all(_finite(x) and 0<=x<=1 for x in values):
        raise ValueError("Invalid YAMNet scores")
    if type(frames) is not int or not 1<=frames<=128:
        raise ValueError("Invalid YAMNet frame count")
    # Dieselbe Prioritaet wie die Referenz, nicht als STT-Veto verwenden.
    event=next((name for name,score,threshold in zip(
        ("cough","throat-clearing","sneeze"),values[1:],(.15,.15,.25))
        if score>=threshold),None)
    return AudioEventEvidence(event,*values,frames)
