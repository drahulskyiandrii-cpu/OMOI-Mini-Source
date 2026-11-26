"""Frühes TTS-Chunking ohne vollständige LLM-Antwort abzuwarten.

Die kurzen Textabschnitte dürfen nur nach vollständigen Wörtern/Satzgrenzen
freigegeben werden. Das Modell streamt weiter, während TTS bereits arbeitet.
"""
from __future__ import annotations
import re

# Keine Wortmitten abtrennen; kurze Phrasen eher zusammenhalten.
_BOUNDARY = re.compile(r"(?<=[.!?;…])\s+|\n+")


class SpeechChunker:
    def __init__(self, first_target:int=24, max_chars:int=170):
        if not 1<=first_target<=max_chars<=1800:
            raise ValueError("Invalid speech chunk limits")
        self.pending=""
        self.first_target=first_target
        self.max_chars=max_chars
        self.emitted=False

    def feed(self,delta:str)->list[str]:
        if not isinstance(delta,str) or not delta:return []
        self.pending+=delta
        chunks=[]
        while self.pending:
            boundary=None
            target=self.first_target if not self.emitted else 58
            limit=min(self.max_chars,105 if not self.emitted else self.max_chars)
            for m in _BOUNDARY.finditer(self.pending):
                if m.start()>self.max_chars:break
                if m.start()>=target:
                    boundary=m.end()
                    break
            if boundary is not None:
                chunks.append(self.pending[:boundary].strip())
                self.pending=self.pending[boundary:]
                self.emitted=True
                continue
            if len(self.pending)<limit:
                break
            at=self.pending.rfind(" ",target,limit+1)
            if at<=0:
                at=self.pending.find(" ",limit,self.max_chars+1)
            if at<=0:
                # Normale lange Woerter nicht trennen. Nur ein einzelnes
                # ueberlanges Token darf die harte TTS-Grenze nicht sprengen.
                at=self.pending.rfind(" ",0,self.max_chars+1)
                if at<=0:
                    if len(self.pending)<=self.max_chars:break
                    at=self.max_chars
            chunks.append(self.pending[:at].strip())
            self.pending=self.pending[at:].lstrip()
            self.emitted=True
        return [x for x in chunks if x]

    def finish(self)->list[str]:
        text=self.pending.strip()
        self.pending=""
        self.emitted=False
        return [text] if text else []
