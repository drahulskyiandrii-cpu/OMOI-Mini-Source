"""Wiederverwendbarer lokaler STT-Worker: OpenVINO/Whisper per Node.js, ohne Cloud."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import json
import queue
import subprocess
from .external_process import spawn_external,stop_owned_process,read_worker_events,signal_pipe_failure,finish_worker_pipes
import threading
import time
from pathlib import Path
from .providers import ProviderFailure
from .core import external_runtime_root, omoi_model_root, legacy_development_enabled
from .runtime_config import runtime_paths, runtime_environment, configured_worker_root
from .stt_quality import auto_language_issue,normalize_language

class LocalWhisper:
    def __init__(self,engine:str="openvino",runtime_overrides=None):
        if engine not in ("openvino","cuda"):
            raise ValueError("UNKNOWN_LOCAL_STT_ENGINE")
        self.engine=engine
        self.runtime_overrides=dict(runtime_overrides or {})
        self.worker=(configured_worker_root(self.runtime_overrides) or external_runtime_root())/"local-stt"/("cuda_worker.py" if engine=="cuda" else "worker.mjs")
        self.proc:subprocess.Popen|None=None
        self.reader:threading.Thread|None=None
        self.writer:threading.Thread|None=None
        self.messages:queue.Queue=queue.Queue(maxsize=16)
        self.lock=threading.Lock()
        self._lifecycle=threading.Lock()
        self._shutdown=threading.Event()
        self.serial=0
        self.ready=False
        self.last_uncertain=False
        self.last_language=None
        self.last_decode_ms=None
        self.last_load_ms=None
    def _read_output(self,proc,messages):
        read_worker_events(proc,messages,64*1024)


    def is_ready(self):
        proc=self.proc
        return self.ready and proc is not None and proc.poll() is None

    def _request(self,proc,messages,request,timeout,is_active=None):
        # Both the pipe write and response wait share one deadline.
        # A wedged stdin must not hold the STT lock forever.
        deadline=time.monotonic()+timeout
        written=queue.Queue(maxsize=1)
        def write():
            try:
                proc.stdin.write(json.dumps(request)+"\n")
                proc.stdin.flush()
                written.put(True)
            except (OSError,ValueError,AttributeError):
                written.put(False)
        with self._lifecycle:
            self.writer=threading.Thread(target=write,name="omoi-stt-write",daemon=True)
            self.writer.start()
        def wait(q):
            while True:
                if self._shutdown.is_set() or (is_active is not None and not is_active()):
                    self.close()
                    raise ProviderFailure("Spracherkennung abgebrochen.")
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    self.close()
                    raise ProviderFailure("Whisper hat nicht rechtzeitig geantwortet")
                try:return q.get(timeout=min(remaining,0.25))
                except queue.Empty:continue
        if not wait(written):
            self.close()
            raise ProviderFailure("Lokale STT-Verbindung unterbrochen")
        reply=wait(messages)
        if reply.get("event")=="fatal" or type(reply.get("id")) is not int or reply.get("id")!=request["id"]:
            self.close()
            raise ProviderFailure("Lokale STT-Verbindung unterbrochen")
        return reply
    def ensure(self,*,is_active=None):
        if self._shutdown.is_set():raise ProviderFailure("Spracherkennung abgebrochen.")
        if self.is_ready():return
        if self.proc is not None:self.close()
        if not self.worker.is_file():raise ProviderFailure("Lokaler STT-Worker fehlt")
        child_env=runtime_environment(self.runtime_overrides)
        if self.engine=="cuda":
            paths=runtime_paths(child_env)
            if (not paths.cuda_python.is_file() or not paths.cuda_overlay.is_dir()
                    or not (paths.cuda_model/"model.bin").is_file()):
                raise ProviderFailure("Lokales Faster-Whisper CUDA ist nicht installiert")
            command=[str(paths.cuda_python),str(self.worker)]
            child_env["OMOI_MINI_CUDA_STT_OVERLAY"]=str(paths.cuda_overlay)
            child_env["OMOI_MINI_CUDA_STT_MODEL"]=str(paths.cuda_model)
            child_env["HF_HUB_OFFLINE"]="1"
            child_env["TRANSFORMERS_OFFLINE"]="1"
        else:
            # Die eigenständige EXE darf das Haupt-OMOI-Modellverzeichnis
            # weder durchsuchen noch als stillen Standard akzeptieren.
            configured=child_env.get("OMOI_MINI_STT_MODEL","").strip()
            if not configured:
                if not legacy_development_enabled():
                    raise ProviderFailure(
                        "OpenVINO-Modellpfad fehlt: OMOI_MINI_STT_MODEL konfigurieren."
                    )
                # Nur der akzeptierte Source-Demostand behält den Legacy-Pfad.
                configured=str(omoi_model_root()/"local-models"/"openvino-whisper-medium-int8-ov")
            model_path=Path(configured).expanduser()
            if not legacy_development_enabled() and not model_path.is_dir():
                raise ProviderFailure("OpenVINO-Modellverzeichnis ist nicht vorhanden.")
            child_env["OMOI_MINI_STT_MODEL"]=str(model_path)
            local_modules=self.worker.parent/"node_modules"/"openvino-genai-node"
            if not local_modules.is_dir():
                raise ProviderFailure("OpenVINO-Node-Paket fehlt. Bitte local-stt installieren.")
            command=["node",str(self.worker)]
        child_env["PYTHONIOENCODING"]="utf-8"
        try:
            with self._lifecycle:
                if self._shutdown.is_set() or (is_active is not None and not is_active()):
                    raise ProviderFailure("Spracherkennung abgebrochen.")
                self.messages=queue.Queue(maxsize=16)
                self.proc=spawn_external(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,encoding="utf-8",errors="replace",bufsize=1,
                    cwd=str(self.worker.parent),env=child_env,
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                proc,messages=self.proc,self.messages
                self.reader=threading.Thread(target=self._read_output,args=(proc,messages),name="omoi-stt-reader",daemon=True)
                self.reader.start()
        except OSError:
            # Ohne diese Behandlung verschwand FileNotFoundError im Pool-
            # Thread: die GUI zeigte ewig "Erkennt Sprache" ohne Fehler.
            if self.engine=="openvino":
                raise ProviderFailure(
                    "OpenVINO-STT konnte nicht gestartet werden. Node.js-Pfad prüfen."
                ) from None
            raise ProviderFailure(
                "CUDA-STT konnte nicht gestartet werden. Lokale Python-Laufzeit prüfen."
            ) from None
        deadline=time.monotonic()+95
        while True:
            if self._shutdown.is_set() or (is_active is not None and not is_active()):
                self.close()
                raise ProviderFailure("Spracherkennung abgebrochen.")
            remaining=deadline-time.monotonic()
            if remaining<=0:
                self.close()
                raise ProviderFailure("Whisper konnte nicht gestartet werden")
            try:
                event=messages.get(timeout=min(remaining,.25))
                break
            except queue.Empty:continue
        if event.get("event")!="ready":
            self.close()
            engine="CUDA Faster-Whisper" if self.engine=="cuda" else "OpenVINO-Whisper"
            raise ProviderFailure(f"{engine} ist nicht verfügbar")
        with self._lifecycle:
            if self._shutdown.is_set() or self.proc is not proc or proc.poll() is not None:
                raise ProviderFailure("Lokale STT-Verbindung unterbrochen")
            self.ready=True
            self.last_load_ms=self._valid_ms(event.get("loadMs"))

    @staticmethod
    def _valid_ms(value):
        return value if type(value) is int and 0<=value<=180000 else None

    @contextmanager
    def _request_lock(self,is_active):
        while not self.lock.acquire(timeout=.1):
            if self._shutdown.is_set() or (is_active is not None and not is_active()):
                raise ProviderFailure("Spracherkennung abgebrochen.")
        try:yield
        finally:self.lock.release()

    def transcribe(self,wav_bytes:bytes,language:str,*,options=None,partial=False,is_active=None)->str:
        if len(wav_bytes)>16*1024*1024:raise ProviderFailure("Audio zu groß für lokale STT")
        with self._request_lock(is_active):
            self.last_uncertain=False
            self.last_language=None
            self.last_decode_ms=None
            self.last_load_ms=None
            if self._shutdown.is_set():
                raise ProviderFailure("Spracherkennung abgebrochen.")
            if partial and not self.is_ready():
                raise ProviderFailure("STT noch nicht bereit")
            if is_active is not None and not is_active():
                raise ProviderFailure("Spracherkennung abgebrochen.")
            if is_active is None:self.ensure()
            else:self.ensure(is_active=is_active)
            if is_active is not None and not is_active():
                raise ProviderFailure("Spracherkennung abgebrochen.")
            self.serial+=1
            rid=self.serial
            request={"id":rid,"audio":base64.b64encode(wav_bytes).decode("ascii"),"language":language}
            if options and self.engine=="cuda":
                request["options"]=options
            proc,messages=self.proc,self.messages
            if not proc or not proc.stdin:raise ProviderFailure("STT-Prozess nicht verfügbar")
            reply=self._request(proc,messages,request,5 if partial else 25,is_active)
            if reply.get("ok") is not True:
                raise ProviderFailure("Lokale Spracherkennung fehlgeschlagen")
            self.last_uncertain=reply.get("uncertain") is True
            text=reply.get("text")
            if not isinstance(text,str) or len(text)>4000:
                self.close()
                raise ProviderFailure("Ungueltige lokale STT-Antwort")
            text=text.strip()
            if not text:raise ProviderFailure("Keine Sprache erkannt")
            detected=normalize_language(reply.get("language"))
            if language=="auto":
                if detected not in ("de","ru","en") or auto_language_issue(detected,text):
                    raise ProviderFailure("Automatische Spracherkennung unsicher. Bitte Deutsch oder Russisch fest einstellen.")
            elif detected is not None and detected!=language:
                raise ProviderFailure("Lokale Spracherkennung meldet eine andere Sprache.")
            self.last_language=detected or language
            self.last_decode_ms=self._valid_ms(reply.get("wallMs"))
            return text
    def unload_if(self,unused):
        with self.lock:
            if unused():self.close()

    def shutdown(self):
        self._shutdown.set()
        self.close()

    def close(self):
        with self._lifecycle:
            self.ready=False
            signal_pipe_failure(self.messages)
            # Bei einem Fehler bleibt das Handle fuer einen spaeteren Versuch.
            stop_owned_process(self.proc,tree=self.engine=="cuda")
            finish_worker_pipes(self.proc,self.reader,self.writer)
            self.proc=None
            self.reader=self.writer=None
