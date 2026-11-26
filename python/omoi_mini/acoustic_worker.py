"""Explizite Offline-Qualifikation; nicht vom Controller aufgerufen."""
from __future__ import annotations

from dataclasses import dataclass
import math
import base64
import json
import queue
import threading
import time
import subprocess
from pathlib import Path

from .acoustic_policy import silero_evidence,ecapa_evidence,yamnet_evidence
from .external_process import spawn_external,stop_owned_process,read_worker_events,finish_worker_pipes
from .runtime_config import runtime_environment
from .providers import ProviderFailure


MODES={"silero":("silero",),"ecapa":("ecapa",),"yamnet":("yamnet",),
       "both":("silero","ecapa"),"all":("silero","ecapa","yamnet")}


@dataclass(frozen=True)
class AcousticTiming:
    load_ms:int
    inference_ms:int


def validate_result(reply,mode,samples):
    if (reply.get("schema")!=1 or reply.get("id")!=1 or reply.get("ok") is not True
            or reply.get("device")!="CPU" or reply.get("samples")!=samples):
        raise ValueError("Invalid acoustic response")
    selected=MODES[mode]
    result={}
    if "silero" in selected:
        probabilities=reply["silero"]
        if len(probabilities)!=(samples+511)//512:
            raise ValueError("Invalid frame count")
        result["silero"]=silero_evidence(probabilities)
    if "ecapa" in selected:result["ecapa"]=ecapa_evidence(reply["ecapa"])
    if "yamnet" in selected:result["yamnet"]=yamnet_evidence(reply["yamnet"])
    for name in selected:
        load,inference=reply.get(name+"LoadMs"),reply.get(name+"Ms")
        if (type(load) is not int or type(inference) is not int
                or not 0<=load<=120000 or not 0<=inference<=120000):
            raise ValueError("Invalid acoustic timing")
        result[name+"_timing"]=AcousticTiming(load,inference)
    return result


def qualify_pcm(pcm:bytes,worker:Path,*,mode, silero:Path|None=None,
                ecapa:Path|None=None, yamnet:Path|None=None, allow_models=False, is_active=lambda:True,
                timeout=60):
    """Ein eigener CPU-Prozess pro Messung, keine dauerhafte Modellresidenz."""
    if allow_models is not True:
        raise ProviderFailure("Akustik-Modelltest nicht freigegeben.")
    if mode not in MODES or not isinstance(pcm,bytes) or len(pcm)%2 or not 3200<=len(pcm)<=960000:
        raise ValueError("Invalid acoustic input")
    if not is_active():raise ProviderFailure("Akustik-Test abgebrochen.")
    for name,model in (("silero",silero),("ecapa",ecapa),("yamnet",yamnet)):
        if name in MODES[mode] and (model is None or not model.is_absolute() or not model.is_file() or model.is_symlink()):
            raise ProviderFailure("Akustik-Modellpfad fehlt oder ist ungueltig.")
    worker=Path(worker)
    if not worker.is_file() or worker.is_symlink():
        raise ProviderFailure("Akustik-Worker fehlt.")
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=120:raise ValueError("Invalid acoustic timeout")
    payload=json.dumps(dict(schema=1,id=1,mode=mode,pcm=base64.b64encode(pcm).decode("ascii")))
    messages=queue.Queue(maxsize=4)
    proc=None
    reader=writer=None
    try:
        proc=spawn_external(["node",str(worker.resolve()),"--allow-models",
                             str(silero or ""),str(ecapa or ""),str(yamnet or "")],
                             cwd=str(worker.resolve().parent),env=runtime_environment(),
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                             encoding="utf-8",errors="strict",
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        def send():
            try:
                proc.stdin.write(payload)
                proc.stdin.close()
            except (OSError,ValueError):pass
        reader=threading.Thread(target=read_worker_events,args=(proc,messages,65536),daemon=True)
        writer=threading.Thread(target=send,daemon=True)
        reader.start()
        writer.start()
        deadline=time.monotonic()+timeout
        while is_active():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ProviderFailure("Akustik-Test Zeitlimit.")
            try:reply=messages.get(timeout=min(.1,remaining))
            except queue.Empty:continue
            if not is_active():break
            return validate_result(reply,mode,len(pcm)//2)
        raise ProviderFailure("Akustik-Test abgebrochen.")
    except (OSError,ValueError,KeyError,TypeError,OverflowError):
        raise ProviderFailure("Akustik-Test fehlgeschlagen.") from None
    finally:
        if proc is not None:
            stop_owned_process(proc)
            finish_worker_pipes(proc,reader,writer)
