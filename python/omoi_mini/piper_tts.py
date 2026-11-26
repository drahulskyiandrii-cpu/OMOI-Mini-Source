"""Isolierter lokaler Piper-Stdio-Adapter; keine HTTP- oder Cloud-Abhängigkeit."""
from __future__ import annotations
import base64
import wave
from .pcm_wave import validate_pcm_wav
from contextlib import contextmanager
import json
import queue
import subprocess
from .external_process import spawn_external,stop_owned_process,read_worker_events,signal_pipe_failure,finish_worker_pipes
import threading
import time

from .core import external_runtime_root
from .runtime_config import runtime_paths, runtime_environment, configured_worker_root
from .providers import ProviderFailure

VOICE_LANGUAGES={
    "de_DE-eva_k-x_low":"de",
    "de_DE-ramona-low":"de",
    "ru_RU-irina-medium":"ru",
}


class PiperTTSProcess:
    def __init__(self,runtime_overrides=None):
        self.runtime_overrides=dict(runtime_overrides or {})
        self.proc:subprocess.Popen|None=None
        self.lines:queue.Queue=queue.Queue(maxsize=16)
        self.reader:threading.Thread|None=None
        self.writer:threading.Thread|None=None
        self.lock=threading.Lock()
        self.counter=0
        self._lifecycle=threading.Lock()
        self._closed=threading.Event()
        self.ready=False

    def _read_lines(self,proc,lines):
        read_worker_events(proc,lines,24*1024*1024)


    def _ensure(self,is_active=lambda:True):
        if self._closed.is_set():raise ProviderFailure("Sprachausgabe wird beendet.")
        if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
        if self.ready and self.proc is not None and self.proc.poll() is None:return
        if self.proc is not None:self.close()
        root=configured_worker_root(self.runtime_overrides) or external_runtime_root()
        worker=root/"local-tts"/"piper_worker.py"
        env=runtime_environment(self.runtime_overrides)
        python=runtime_paths(env).piper_python
        if not worker.is_file() or not python.is_file():
            raise ProviderFailure("Piper ist noch nicht installiert (lokale TTS-Stimme).")
        env["PYTHONIOENCODING"]="utf-8"
        with self._lifecycle:
            if self._closed.is_set():raise ProviderFailure("Sprachausgabe wird beendet.")
            if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
            self.lines=queue.Queue(maxsize=16)
            self.proc=spawn_external([str(python),str(worker)],
                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                 stderr=subprocess.DEVNULL,encoding="utf-8",errors="replace",
                 cwd=str(worker.parent),env=env,bufsize=1,
                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            proc,lines=self.proc,self.lines
            self.reader=threading.Thread(target=self._read_lines,args=(proc,lines),name="omoi-piper-reader",daemon=True)
            self.reader.start()
        deadline=time.monotonic()+15
        while True:
            if self._closed.is_set() or not is_active():
                self.close()
                raise ProviderFailure("Sprachausgabe abgebrochen.")
            remaining=deadline-time.monotonic()
            if remaining<=0:
                self.close()
                raise ProviderFailure("Piper startet zu langsam.")
            try:
                ready=lines.get(timeout=min(.1,remaining))
                break
            except queue.Empty:continue
        if ready.get("event")!="ready":
            self.close()
            raise ProviderFailure("Piper-TTS konnte nicht geladen werden.")
        with self._lifecycle:
            if self._closed.is_set() or self.proc is not proc or proc.poll() is not None:
                raise ProviderFailure("Piper-Verbindung wurde unterbrochen")
            self.ready=True

    @contextmanager
    def _request_lock(self,is_active):
        while not self.lock.acquire(timeout=.1):
            if self._closed.is_set() or not is_active():
                raise ProviderFailure("Sprachausgabe abgebrochen.")
        try:yield
        finally:self.lock.release()

    def synthesize(self,text:str,language:str,voice:str,*,is_active=lambda:True)->bytes:
        if not isinstance(text,str) or not 0<len(text)<=1800:
            raise ProviderFailure("Ungültiger Text für Piper-TTS")
        if VOICE_LANGUAGES.get(voice)!=language:
            raise ProviderFailure("Piper-Stimme passt nicht zur ausgewählten Sprache")
        with self._request_lock(is_active):
            self._ensure(is_active)
            if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
            self.counter+=1
            identifier=self.counter
            request={"id":identifier,"text":text,"voice":voice,"language":language}
            with self._lifecycle:proc,lines=self.proc,self.lines
            if proc is None:raise ProviderFailure("Piper-Verbindung wurde unterbrochen")
            written=queue.Queue(maxsize=1)
            deadline=time.monotonic()+25
            def write():
                try:
                    proc.stdin.write(json.dumps(request)+"\n")
                    proc.stdin.flush()
                    written.put(True)
                except (OSError,ValueError,AttributeError):written.put(False)
            with self._lifecycle:
                self.writer=threading.Thread(target=write,name="omoi-piper-write",daemon=True)
                self.writer.start()
            def wait(q):
                while True:
                    if self._closed.is_set() or not is_active():
                        self.close()
                        raise ProviderFailure("Sprachausgabe abgebrochen.")
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        self.close()
                        raise ProviderFailure("Piper-TTS antwortet zu langsam")
                    try:return q.get(timeout=min(.25,remaining))
                    except queue.Empty:continue
            if not wait(written):
                self.close()
                raise ProviderFailure("Piper-Verbindung wurde unterbrochen")
            reply=wait(lines)
            if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
            if type(reply.get("id")) is not int or reply.get("id")!=identifier or reply.get("event")=="fatal":
                self.close()
                raise ProviderFailure("Piper-Verbindung wurde unterbrochen")
            if reply.get("ok") is not True:
                raise ProviderFailure("Piper-TTS konnte die Stimme nicht generieren")
            if not isinstance(reply.get("audio"),str) or len(reply["audio"])>24*1024*1024:
                raise ProviderFailure("Piper-Audio zu gross oder ungueltig")
            try:audio=base64.b64decode(reply["audio"],validate=True)
            except (ValueError,KeyError,TypeError):
                raise ProviderFailure("Piper-TTS meldete beschädigtes Audio") from None
            try:validate_pcm_wav(audio,max_bytes=16*1024*1024)
            except (ValueError,EOFError,wave.Error):
                raise ProviderFailure("Piper-TTS meldete keine PCM-WAV") from None
            return audio

    def unload_if(self,unused):
        with self.lock:
            if unused():self.close()

    def shutdown(self):
        self._closed.set()
        self.close()

    def close(self):
        with self._lifecycle:
            self.ready=False
            signal_pipe_failure(self.lines)
            stop_owned_process(self.proc,tree=True)
            finish_worker_pipes(self.proc,self.reader,self.writer)
            self.proc=None
            self.reader=self.writer=None
