"""Loopback-Adapter für Qwen3-TTS mit privater lokaler Stimmreferenz.

Keine Online-Fallbacks und keine persönlichen Stimmenkennungen im Quellcode.
"""
from __future__ import annotations

import os
import socket
import wave
from .pcm_wave import validate_pcm_wav
import asyncio
from contextlib import closing,contextmanager
import hashlib
import subprocess
from .external_process import spawn_external,stop_owned_process
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import httpx

from .providers import ProviderFailure,cancellable_post,cancellable_events,validate_loopback_endpoint
from .core import external_runtime_root
from .runtime_config import runtime_paths, runtime_environment, configured_worker_root

def local_tts_model_id(variant: str = "base") -> str:
    if variant == "cf050":
        return "qwen3-tts-1.7b-base-cf050"
    if variant != "base":
        raise ProviderFailure("Unbekannte Qwen-Variante")
    size = os.environ.get("OMOI_MINI_QWEN_SIZE", "1.7B")
    if size not in ("0.6B", "1.7B"):
        raise ProviderFailure("Unbekannte lokale Qwen-Modellgröße")
    return "qwen3-tts-"+size.lower()+"-base"


def local_tts_default_speed(lang: str, variant: str = "base") -> float:
    # Die erhoehte Sprechgeschwindigkeit wurde nur fuer Qwen 0.6B mit russischem Text evaluiert.
    return 1.15 if lang == "ru" and local_tts_model_id(variant) == "qwen3-tts-0.6b-base" else 1.0
SUPPORTED_EMOTIONS = frozenset({"neutral","happy","amused","sad","excited"})
SUPPORTED_LANGUAGES = frozenset({"de","ru","en"})

_endpoint_lock = threading.Lock()
_auto_endpoint: str | None = None

def _endpoint() -> str:
    """One loopback port per Mini process, unless explicitly configured."""
    configured = os.environ.get("OMOI_MINI_TTS_ENDPOINT", "").strip()
    if configured:
        return validate_loopback_endpoint(configured.rstrip("/"), "TTS")
    global _auto_endpoint
    with _endpoint_lock:
        if _auto_endpoint is None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind(("127.0.0.1", 0))
                    _auto_endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
            except OSError:
                raise ProviderFailure("Freier lokaler TTS-Port nicht verfuegbar") from None
        return _auto_endpoint


def local_tts_wav(text:str,language:str="de",emotion:str="neutral",*,variant:str="base",is_active=lambda:True)->bytes|None:
    if not isinstance(text,str) or not 0<len(text)<=2000:
        raise ProviderFailure("TTS-Text ist ungültig oder zu lang")
    if language not in SUPPORTED_LANGUAGES:
        raise ProviderFailure("TTS-Sprache wird nicht unterstützt")
    if emotion not in SUPPORTED_EMOTIONS:
        raise ProviderFailure("Unbekannte TTS-Emotion")
    # Nika ist eine Cartesia-Stimme und keine automatisch übertragbare lokale Stimme.
    data={"model":local_tts_model_id(variant),"input":text,"voice":"reference","language":language,
          "emotion":emotion,"speed":local_tts_default_speed(language,variant),"response_format":"wav"}
    deadline=time.monotonic()+40
    while is_active():
        try:
            response=cancellable_post(_endpoint()+"/v1/audio/speech",json=data,
                is_active=is_active,timeout=httpx.Timeout(180.0,connect=4.0),trust_env=False)
        except (httpx.TimeoutException,httpx.ConnectError,httpx.NetworkError):
            raise ProviderFailure("Lokaler TTS-Server ist nicht erreichbar oder zu langsam") from None
        if response is None or not is_active():return None
        if response.status_code!=429:break
        if time.monotonic()>=deadline:
            raise ProviderFailure("Lokale Qwen-Inferenz bleibt laenger als 40 Sekunden belegt")
        time.sleep(.12)
    else:return None
    if response.status_code >= 400:
        raise ProviderFailure(f"Lokaler TTS: HTTP {response.status_code}")
    if variant!="base" and response.headers.get("X-OMOI-TTS-Model")!=local_tts_model_id(variant):
        raise ProviderFailure("Unerwartete TTS-Modellvariante")
    audio=response.content
    try:validate_pcm_wav(audio)
    except (ValueError,EOFError,wave.Error):
        raise ProviderFailure("Lokaler TTS hat keine gueltige WAV-Antwort geliefert") from None
    return audio


async def _pcm_http_chunks(payload,variant):
    """429 nur vor der ersten Audioausgabe wiederholen."""
    deadline=time.monotonic()+40
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0,connect=4.0),trust_env=False) as client:
        while True:
            async with client.stream("POST",_endpoint()+"/v1/audio/stream",json=payload) as response:
                if response.status_code!=429:
                    if response.status_code>=400:
                        raise ProviderFailure(f"Lokaler TTS-Stream: HTTP {response.status_code}")
                    if (response.headers.get("X-OMOI-PCM-Rate")!="24000"
                            or response.headers.get("X-OMOI-PCM-Format")!="s16le"
                            or response.headers.get("X-OMOI-TTS-Model")!=local_tts_model_id(variant)):
                        raise ProviderFailure("Ungueltiges PCM-Stream-Format")
                    async for data in response.aiter_bytes():
                        yield data
                    return
            if time.monotonic()>=deadline:
                raise ProviderFailure("Lokale Qwen-Inferenz bleibt laenger als 40 Sekunden belegt")
            await asyncio.sleep(.12)


def local_tts_pcm_stream(
    text: str, language: str, emotion: str,
    on_pcm, is_active=lambda: True, *, variant: str = "base",
) -> bytes | None:
    """Erste PCM-Chunks sofort weiterleiten, vollständige Bytes für WAV-Cache sammeln.

    Ausschließlich Native-Speed (1.0); die 0.6B-RU-Tempo-Option nutzt weiter
    vollständiges WAV mit atempo. Ein Abbruch speichert keine Teil-Audiodatei.
    """
    import struct

    if (not isinstance(text, str) or not 0 < len(text) <= 2000
            or language not in SUPPORTED_LANGUAGES
            or emotion not in SUPPORTED_EMOTIONS):
        raise ProviderFailure("Ungültiger Streaming-TTS-Auftrag")
    if local_tts_default_speed(language,variant) != 1.0:
        raise ProviderFailure("Streaming benötigt Qwen-Nativtempo")
    payload = {
        "model": local_tts_model_id(variant), "input": text, "voice": "reference",
        "language": language, "emotion": emotion, "speed": 1.0,
        "response_format": "pcm",
    }
    if not is_active():
        return None
    parts = []
    total = 0
    pending = bytearray()
    finished = False
    try:
        with closing(cancellable_events(_pcm_http_chunks(payload,variant),is_active,
                                        total_timeout=220)) as chunks:
            for data in chunks:
                if not is_active():return None
                pending.extend(data)
                while len(pending) >= 4:
                    length = struct.unpack(">I", pending[:4])[0]
                    if length == 0xFFFFFFFF:
                        raise ProviderFailure("Qwen hat die Streaming-Synthese abgebrochen")
                    if length == 0:
                        if len(pending) != 4:
                            raise ProviderFailure("Unerwartete Daten nach PCM-Ende")
                        finished = True
                        pending.clear()
                        break
                    if length > 1024 * 1024 or length % 2:
                        raise ProviderFailure("Ungültige PCM-Chunk-Länge")
                    if len(pending) < length + 4:
                        break
                    pcm = bytes(pending[4:4 + length])
                    del pending[:4 + length]
                    total += len(pcm)
                    if total > 32 * 1024 * 1024:
                        raise ProviderFailure("PCM-Audio überschreitet die Größenbegrenzung")
                    parts.append(pcm)
                    if is_active():
                        on_pcm(pcm)
                    else:
                        return None
                if finished:
                    break
    except ProviderFailure:
        raise
    except (httpx.TimeoutException, httpx.TransportError, ValueError):
        raise ProviderFailure("Lokaler TTS-Stream ist nicht erreichbar") from None
    if not is_active():return None
    if not finished or total < 200 or pending:
        raise ProviderFailure("Unvollständige PCM-Streaming-Antwort")
    return b"".join(parts)


class LocalTTSProcess:
    """Besitzt nur seinen selbst gestarteten Loopback-TTS-Prozess.

    Ein bereits laufender fremder/extern gestarteter Dienst wird niemals beendet.
    """
    def __init__(self,reference_path:str="",runtime_overrides=None,variant:str="base"):
        self.variant=variant
        if variant not in ("base","cf050"):raise ProviderFailure("Unbekannte Qwen-Variante")
        self.runtime_overrides=dict(runtime_overrides or {})
        self.process:subprocess.Popen | None = None
        self.reference_path=reference_path
        self._lock=threading.Lock()
        self._closed=threading.Event()

    def _ready(self,endpoint:str)->bool:
        try:
            with httpx.Client(timeout=1.5,trust_env=False) as client:
                response=client.get(endpoint+"/health")
            if response.status_code==200:
                try:meta=response.json()
                except ValueError:
                    raise ProviderFailure("Ungueltige lokale TTS-Diagnose") from None
                if not isinstance(meta,dict):
                    raise ProviderFailure("Ungueltige lokale TTS-Diagnose")
                if meta.get("service")!="omoi-mini-local-tts":
                    raise ProviderFailure("Auf diesem Port läuft ein anderer Dienst")
                if meta.get("model") != local_tts_model_id(self.variant):
                    raise ProviderFailure("Lokaler TTS-Port gehört einem anderen Modell")
                if self.reference_path:
                    expected=hashlib.sha256(str(Path(self.reference_path).resolve()).encode("utf-8")).hexdigest()[:12]
                    if meta.get("voice_profile") != expected:
                        raise ProviderFailure("Andere Stimme aktiv. Bitte die lokale TTS-Laufzeit neu starten.")
                return True
        except httpx.HTTPError:
            return False
        return False

    @contextmanager
    def _startup_lock(self,is_active):
        while not self._lock.acquire(timeout=.1):
            if self._closed.is_set() or not is_active():
                raise ProviderFailure("Sprachausgabe abgebrochen.")
        try:yield
        finally:self._lock.release()

    def ensure(self,is_active=lambda:True)->None:
        local_tts_model_id(self.variant)
        address=_endpoint()
        with self._startup_lock(is_active):
            if self._closed.is_set():
                raise ProviderFailure("Sprachausgabe wird beendet.")
            if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
            if self.process is not None and self.process.poll() is not None:
                self.process=None
            try:
                ready=self._ready(address)
            except ProviderFailure:
                if self.process is not None:self._stop_owned()
                raise
            if ready:
                self._check_active(is_active)
                if self.process is not None and self.process.poll() is not None:
                    self._stop_owned()
                    raise ProviderFailure("Lokaler TTS-Prozess beendet sich beim Start")
                return
            if self.process is None:
                root=configured_worker_root(self.runtime_overrides) or external_runtime_root()
                child_env=runtime_environment(self.runtime_overrides)
                python=runtime_paths(child_env).qwen_python
                server=root/"local-tts"/"qwen_server.py"
                if not python.is_file() or not server.is_file():
                    raise ProviderFailure("Lokale Qwen3-TTS-Laufzeit fehlt")
                child_env["OMOI_MINI_TTS_PORT"]=str(urlparse(address).port)
                child_env["OMOI_MINI_QWEN_VARIANT"]=self.variant
                child_env["OMOI_MINI_QWEN_SIZE"]="0.6B" if local_tts_model_id(self.variant)=="qwen3-tts-0.6b-base" else "1.7B"
                if self.reference_path:
                    from .voice_assets import validate_voice_wav
                    from .core import user_data_dir
                    reference=Path(self.reference_path).resolve()
                    if not reference.is_relative_to((user_data_dir()/"voices").resolve()):
                        raise ProviderFailure("Stimmdatei liegt außerhalb des Mini-Profils")
                    try:
                        validate_voice_wav(reference)
                    except ValueError:
                        raise ProviderFailure("Gespeicherte WAV-Stimmreferenz ist ungültig") from None
                    child_env["OMOI_MINI_QWEN_REFERENCE"]=str(reference)
                child_env["HF_HUB_OFFLINE"]="1"
                child_env["TRANSFORMERS_OFFLINE"]="1"
                if self._closed.is_set():
                    raise ProviderFailure("Sprachausgabe wird beendet.")
                if not is_active():raise ProviderFailure("Sprachausgabe abgebrochen.")
                try:
                    self.process=spawn_external([str(python),str(server)],
                        cwd=str(server.parent),env=child_env,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                except OSError:
                    raise ProviderFailure("Lokale Qwen-Python-Laufzeit konnte nicht gestartet werden.") from None
            try:
                self._wait_ready(address,is_active)
            except ProviderFailure:
                # Keep warm services on ordinary turn cancellation, but do not
                # leave a failed/cancelled startup running in the background.
                self._stop_owned()
                raise

    def _check_active(self,is_active):
        if self._closed.is_set():
            raise ProviderFailure("Sprachausgabe wird beendet.")
        if not is_active():
            raise ProviderFailure("Sprachausgabe abgebrochen.")

    def _wait_ready(self,address,is_active):
        until=time.monotonic()+45
        while time.monotonic()<until:
            self._check_active(is_active)
            if self.process.poll() is not None:
                raise ProviderFailure(f"Lokaler TTS-Prozess beendet (Exit {self.process.returncode}). Port und Python-Laufzeit pruefen.")
            if self._ready(address):
                self._check_active(is_active)
                if self.process.poll() is not None:
                    raise ProviderFailure("Lokaler TTS-Prozess beendet sich beim Start")
                return
            self._closed.wait(0.2)
        raise ProviderFailure("Lokaler TTS-Server startet nicht rechtzeitig")

    def _stop_owned(self):
        stop_owned_process(self.process,tree=True,timeout=5)
        self.process=None

    def prewarm(self,is_active=lambda:True)->None:
        """Manueller Warmup der bereits ausgewählten Qwen-Stimme (nur Loopback)."""
        address=_endpoint()
        active=lambda:not self._closed.is_set() and is_active()
        self.ensure(active)
        try:
            # A previous cancelled turn can still hold the server's inference lock.
            # Only retry explicit busy responses, never an ambiguous transport error.
            deadline=time.monotonic()+40
            while active():
                response=cancellable_post(address+"/warmup",is_active=active,
                    timeout=httpx.Timeout(180.0,connect=4.0),trust_env=False,
                    max_bytes=64*1024,read_error=True)
                self._check_active(active)
                if response is None:
                    raise ProviderFailure("Sprachausgabe abgebrochen.")
                if response.status_code!=429:break
                if time.monotonic()>=deadline:
                    raise ProviderFailure("Lokale Qwen-Inferenz bleibt laenger als 40 Sekunden belegt")
                self._closed.wait(.12)
            else:
                raise ProviderFailure("Sprachausgabe abgebrochen.")
            body=response.json()
            if not isinstance(body,dict):raise ValueError("Invalid warmup response")
            if response.status_code != 200 or body.get("loaded") is not True:
                detail=body.get("detail")
                messages={
                    "CUDA_UNAVAILABLE":"Qwen: CUDA-GPU nicht verfügbar.",
                    "CUDA_OUT_OF_MEMORY":"Qwen: Nicht genug GPU-Speicher.",
                    "PRIVATE_VOICE_REFERENCE_MISSING":"Qwen: Stimmreferenz fehlt.",
                    "CF050_ADAPTER_INVALID":"Qwen: CF50-Adapter ungültig.",
                    "OFFLINE_MODEL_MISSING":"Qwen: Lokale Modelldateien fehlen.",
                    "DEPENDENCY_MISSING":"Qwen: Python-Abhängigkeit fehlt.",
                    "GENERATION_IN_PROGRESS":"Qwen ist bereits beschäftigt.",
                }
                raise ProviderFailure(messages.get(detail,"Qwen3-TTS konnte nicht vorgewärmt werden") if isinstance(detail,str) else "Qwen3-TTS konnte nicht vorgewärmt werden")
        except (httpx.HTTPError,ValueError):
            raise ProviderFailure("Qwen3-TTS-Vorwärmung ist fehlgeschlagen") from None

    def unload_if(self,unused):
        with self._lock:
            if not unused():return
            self._stop_owned()

    def close(self)->None:
        self._closed.set()
        self.unload_if(lambda:True)
