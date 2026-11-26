"""Schmale Cloud-/Lokal-Adapter. Geheimnisse niemals protokollieren."""
from __future__ import annotations
import io
import asyncio
from contextlib import closing, contextmanager
import json
import os
import subprocess
from .external_process import spawn_external,stop_owned_process
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse
import httpx
from .core import omoi_model_root,legacy_development_enabled
from .runtime_config import runtime_environment

class ProviderFailure(Exception):
    pass

def validate_loopback_endpoint(address,provider):
    """Nur eine lokale HTTP-Origin, kein Pfad, Login oder Query."""
    try:
        parsed=urlparse(address)
        if (parsed.scheme!="http" or parsed.hostname not in ("127.0.0.1","localhost")
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("","/") or parsed.query or parsed.fragment
                or not parsed.port or any(c.isspace() or not c.isprintable() for c in address)):
            raise ValueError()
    except ValueError:
        raise ProviderFailure(f"Lokaler {provider}-Endpunkt muss eine Loopback-HTTP-Adresse mit Port sein") from None
    return address.rstrip("/")


def _check_status(response: httpx.Response, provider: str) -> None:
    if response.status_code>=400:
        raise ProviderFailure(f"{provider}: HTTP {response.status_code}")

async def _sse_json(url, payload, headers, timeout, trust_env):
    """Ein HTTP-Stream; der Kontext schliesst auch bei Task-Abbruch."""
    async with httpx.AsyncClient(timeout=timeout,trust_env=trust_env) as client:
        async with client.stream("POST",url,json=payload,headers=headers) as response:
            _check_status(response,"LLM")
            pending=bytearray()
            async for block in response.aiter_bytes():
                # Jede SSE-Zeile begrenzen, auch ohne abschliessendes LF.
                for index,part in enumerate(block.split(b"\n")):
                    if index:
                        line=bytes(pending).rstrip(b"\r")
                        pending.clear()
                        if line.startswith(b"data:"):
                            raw=line[5:].strip()
                            if raw==b"[DONE]":return
                            if raw:
                                event=json.loads(raw)
                                if not isinstance(event,dict):
                                    raise ProviderFailure("Ungueltiges LLM-Streaming-Ereignis")
                                yield event
                    if len(pending)+len(part)>256*1024:
                        raise ProviderFailure("LLM-Streaming-Ereignis zu gross")
                    pending.extend(part)
            # Ein sauberer TCP-EOF ist noch kein vollstaendiger Modell-Stream.
            # Nur [DONE] oder ein explizites Provider-Endereignis darf abschliessen.
            raise ProviderFailure("LLM-Streaming-Verbindung unvollstaendig")



async def _await_active(awaitable,is_active,deadline):
    """Wartet abbrechbar auf einen HTTP-Schritt, ohne einen zweiten Worker."""
    task=asyncio.create_task(awaitable)
    try:
        while is_active():
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise ProviderFailure("HTTP-Zeitlimit erreicht")
            done,_=await asyncio.wait({task},timeout=min(.1,remaining))
            if done:
                return task.result() if is_active() else None
        return None
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)


def cancellable_events(source,is_active,*,total_timeout):
    """Synchroner Iterator fuer asynchrone HTTP-Streams im Worker."""
    deadline=time.monotonic()+total_timeout
    with asyncio.Runner() as runner:
        try:
            while is_active():
                try:event=runner.run(_await_active(anext(source),is_active,deadline))
                except StopAsyncIteration:return
                if event is None:return
                yield event
        finally:
            runner.run(source.aclose())


def _sse_events(url,payload,headers,is_active,*,connect,trust_env):
    source=_sse_json(url,payload,headers,httpx.Timeout(75,connect=connect),trust_env)
    yield from cancellable_events(source,is_active,total_timeout=180)


async def _bounded_post(url,timeout,trust_env,max_bytes,read_error,kwargs):
    async with httpx.AsyncClient(timeout=timeout,trust_env=trust_env) as client:
        async with client.stream("POST",url,**kwargs) as response:
            # Fehlertexte koennen private Inhalte enthalten und werden nicht gelesen.
            if response.status_code>=400 and not read_error:
                return httpx.Response(response.status_code,headers=response.headers)
            data=bytearray()
            async for block in response.aiter_bytes():
                if len(data)+len(block)>max_bytes:
                    raise ProviderFailure("HTTP-Antwort zu gross")
                data.extend(block)
            headers={k:v for k,v in response.headers.items()
                     if k.lower() not in ("content-encoding","content-length","transfer-encoding")}
            return httpx.Response(response.status_code,headers=headers,content=bytes(data))


def cancellable_post(url,*,is_active,timeout,trust_env=True,max_bytes=32*1024*1024,read_error=False,**kwargs):
    if not is_active():return None
    total=timeout if isinstance(timeout,(int,float)) else timeout.read
    with asyncio.Runner() as runner:
        return runner.run(_await_active(_bounded_post(url,timeout,trust_env,max_bytes,read_error,kwargs),
                                        is_active,time.monotonic()+total))


def cloud_answer_stream(messages:list[dict[str,str]],lang:str,is_active=lambda:True)->Iterator[str]:
    """OpenAI-Responses-SSE; nur bei bewusst ausgewähltem Cloud-Anbieter."""
    if not is_active():return
    key=os.environ.get("OPENAI_API_KEY")
    if not key:raise ProviderFailure("OPENAI_API_KEY ist nicht konfiguriert")
    prompt=("Du bist OMOI Mini. Antworte natürlich, knapp und deutlich auf Deutsch."
            if lang=="de" else "Ты OMOI Mini. Отвечай кратко и понятно по-русски.")
    payload={"model":"gpt-6-luna","instructions":prompt,
             "input":[{"role":m["role"],"content":m["content"]} for m in messages[-12:]],
             "max_output_tokens":180,"stream":True}
    try:
        with closing(_sse_events("https://api.openai.com/v1/responses",payload,
                {"Authorization":"Bearer "+key},is_active,connect=7,trust_env=True)) as events:
            for event in events:
                if event.get("type") in ("response.failed","response.cancelled","error"):
                    raise ProviderFailure("OpenAI: Streaming-Antwort fehlgeschlagen")
                if event.get("type")=="response.output_text.delta":
                    part=event.get("delta")
                    if isinstance(part,str) and part:yield part
                if event.get("type") in ("response.completed","response.incomplete"):return
    except (httpx.HTTPError,ValueError,KeyError):
        if is_active():
            raise ProviderFailure("OpenAI: Streaming-Verbindung fehlgeschlagen") from None



# llama.cpp fits unset GPU layers against actual backend free memory.
# The context remains fixed; a retry leaves additional transient headroom.
_GEMMA_RESERVES = (1536, 3072)


def _gemma_command(runtime, model, port, reserve):
    return [str(runtime), "-m", str(model), "--alias", "omoi-managed-chat",
            "--host", "127.0.0.1", "--port", str(port),
            "--ctx-size", "2048", "--parallel", "1", "--batch-size", "1024",
            "--flash-attn", "auto", "--n-gpu-layers", "auto",
            "--fit", "on", "--fit-target", str(reserve),
            "--reasoning", "off", "--reasoning-format", "deepseek",
            "--reasoning-budget", "0"]


def _gemma_environment():
    # Do not inherit a conflicting explicit GPU allocation from other apps.
    return {key: value for key, value in runtime_environment().items()
            if not key.upper().startswith("LLAMA_ARG_")}


class _GemmaStartupLog:
    """Drain stderr continuously, retaining only safe diagnostic flags."""
    def __init__(self, process):
        self.oom = threading.Event()
        self.unsupported = threading.Event()
        self.thread = None
        pipe = getattr(process, "stderr", None)
        if pipe is not None:
            self.thread = threading.Thread(target=self._read, args=(pipe,), daemon=True)
            self.thread.start()

    def _read(self, pipe):
        tail = b""
        try:
            read = getattr(pipe, "read1", pipe.read)
            while True:
                chunk = read(4096)
                if not chunk:
                    break
                data = tail + chunk.lower()
                def new_marker(marker):
                    # Include split markers, but never re-report bytes from the tail alone.
                    return data.find(marker, max(0, len(tail) - len(marker) + 1)) >= 0
                if any(new_marker(marker) for marker in (
                    b"out of memory", b"cuda_error_out_of_memory",
                    b"failed to allocate cuda", b"failed to allocate device buffer",
                )):
                    self.oom.set()
                if any(new_marker(marker) for marker in (
                    b"invalid argument", b"unknown argument", b"unrecognized option",
                )):
                    self.unsupported.set()
                tail = data[-256:]
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def finish(self):
        if self.thread is not None:
            self.thread.join(timeout=1)


class LocalGemma:
    """Eigener, optional automatisch gestarteter llama.cpp-Prozess; niemals fremde Prozesse beenden."""
    def __init__(self,model_file:str="",server_file:str="",*,strict_owned:bool=False):
        self.strict_owned=strict_owned
        self.model_file=model_file
        self.server_file=server_file
        self.endpoint=os.environ.get("OMOI_MINI_LLM_ENDPOINT","http://127.0.0.1:18189").rstrip("/")
        self.process: subprocess.Popen | None = None
        self._startup_log = None
        self.prepare_voice = None
        self._reserve_floor = _GEMMA_RESERVES[0]
        self._refit_lock = threading.Lock()
        self._refit_requested = 0
        self._refit_applied = 0
        self.lock=threading.Lock()
        self._closed=threading.Event()
    def _available(self) -> bool:
        try:
            response=httpx.get(self.endpoint+"/health",timeout=0.8,trust_env=False)
            return response.status_code==200
        except httpx.HTTPError: return False
    @contextmanager
    def _startup_lock(self,is_active):
        while not self.lock.acquire(timeout=.1):
            if self._closed.is_set() or not is_active():
                raise ProviderFailure("LLM-Start abgebrochen.")
        try:yield
        finally:self.lock.release()

    def request_refit(self):
        """Mark a GPU configuration change; never start or stop a model here."""
        with self._refit_lock:
            self._refit_requested += 1

    def _pending_refit(self):
        with self._refit_lock:
            return self._refit_requested != self._refit_applied

    def _apply_refit_locked(self, is_active):
        with self._refit_lock:
            requested = self._refit_requested
            if requested == self._refit_applied:
                return
        self._check_start_active(is_active)
        # Only a stored own process may be stopped; an external service is untouched.
        self._stop_owned()
        with self._refit_lock:
            self._refit_applied = requested

    def release_for_refit(self, is_active=lambda: True):
        """Before a newly selected GPU speech engine allocates, free our old LLM."""
        if not self._pending_refit():
            return
        with self._startup_lock(is_active):
            self._apply_refit_locked(is_active)

    def ensure(self,is_active=lambda:True,prepare_voice=None) -> None:
        if self._closed.is_set():raise ProviderFailure("LLM wird beendet")
        validate_loopback_endpoint(self.endpoint,"LLM")
        # Vor dem Health-Check prüfen: ein anderer Dienst auf demselben Port
        # darf nicht still als eigene LLM der portablen Mini gelten.
        if (self.strict_owned or not legacy_development_enabled()) and not (self.model_file and self.server_file):
            raise ProviderFailure("Bitte eigenes GGUF-Modell und llama-server.exe konfigurieren")
        with self._startup_lock(is_active):
            if self._closed.is_set():raise ProviderFailure("LLM wird beendet")
            if not is_active():raise ProviderFailure("LLM-Start abgebrochen.")
            self._apply_refit_locked(is_active)
            if self._available():
                if not is_active():raise ProviderFailure("LLM-Start abgebrochen.")
                if (self.strict_owned or not legacy_development_enabled()) and (self.process is None or self.process.poll() is not None):
                    raise ProviderFailure("LLM-Port belegt; eigenes Gemma-Profil nicht gestartet")
                return
            if self.process and self.process.poll() is not None: self._stop_owned()
            if self.process is None:
                if self.model_file or self.server_file:
                    if not self.model_file or not self.server_file:
                        raise ProviderFailure("Bitte GGUF-Modell und llama-server.exe auswählen")
                    runtime=Path(self.server_file)
                    model=Path(self.model_file)
                    if (runtime.name.lower()!="llama-server.exe" or not runtime.is_file()
                            or runtime.is_symlink()):
                        raise ProviderFailure("llama-server.exe fehlt oder ist ungültig")
                    if (model.suffix.lower()!=".gguf" or not model.is_file() or model.is_symlink()):
                        raise ProviderFailure("GGUF-Modell fehlt oder ist ungültig")
                    try:
                        with model.open("rb") as handle:handle.read(4)
                        with runtime.open("rb") as handle:handle.read(2)
                    except OSError:
                        raise ProviderFailure("Kein Lesezugriff auf Modell oder Laufzeit") from None
                else:
                    # Nur Quell-Checkout: akzeptierte Entwicklerkonfiguration erhalten.
                    # Frozen wurde bereits vor dem Health-Check abgefangen.
                    runtime=omoi_model_root()/"local-runtimes"/"llama-cpp-vulkan-win-x64"/"llama-server.exe"
                    root=omoi_model_root()/"local-models"/"google-gemma-4-e2b-it-qat-q4_0-gguf"
                    models=sorted(root.glob("*.gguf")) if root.is_dir() else []
                    if not runtime.is_file() or not models:
                        raise ProviderFailure("Bitte eigenes GGUF-Modell und llama-server.exe konfigurieren")
                    model=models[0]
                port=urlparse(self.endpoint).port or 18189
                if prepare_voice is not None:
                    prepare_voice(is_active)
                reserves = tuple(r for r in _GEMMA_RESERVES if r >= self._reserve_floor)
                for attempt, reserve in enumerate(reserves):
                    self._check_start_active(is_active)
                    try:
                        self.process = spawn_external(
                            _gemma_command(runtime, model, port, reserve),
                            cwd=str(runtime.parent), stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, env=_gemma_environment(),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    except OSError:
                        raise ProviderFailure("Gemma-Server konnte nicht gestartet werden") from None
                    self._startup_log = _GemmaStartupLog(self.process)
                    try:
                        self._wait_ready(is_active)
                        self._reserve_floor = reserve
                        self._startup_log.oom.clear()
                        return
                    except ProviderFailure:
                        log = self._startup_log
                        self._stop_owned()
                        if self._closed.is_set() or not is_active():
                            self._check_start_active(is_active)
                        if log.oom.is_set():
                            if attempt + 1 < len(reserves):
                                self._reserve_floor = reserves[attempt + 1]
                                continue
                            raise ProviderFailure("Gemma: GPU-Speicher reicht auch mit Speicherreserve nicht aus.") from None
                        if log.unsupported.is_set():
                            raise ProviderFailure("Gemma: llama.cpp-Startparameter nicht unterstuetzt. Aktuelle Laufzeit mit Auto-fit verwenden.") from None
                        raise
            else:
                try:
                    self._wait_ready(is_active)
                except ProviderFailure:
                    self._stop_owned()
                    raise

    def _check_start_active(self, is_active):
        if self._closed.is_set():
            raise ProviderFailure("LLM wird beendet")
        if not is_active():
            raise ProviderFailure("LLM-Start abgebrochen.")

    def _wait_ready(self, is_active):
        deadline = time.monotonic() + (120 if self.strict_owned else 55)
        while time.monotonic() < deadline:
            self._check_start_active(is_active)
            if self.process.poll() is not None:
                raise ProviderFailure("Gemma-Server beendet sich beim Start")
            try:
                res = httpx.get(self.endpoint + "/health", timeout=1, trust_env=False)
                if res.status_code == 200:
                    self._check_start_active(is_active)
                    if self.process.poll() is not None:
                        raise ProviderFailure("Gemma-Server beendet sich beim Start")
                    return
            except httpx.HTTPError:
                pass
            self._closed.wait(0.2)
        raise ProviderFailure("Gemma-Start dauert zu lange")

    def _stop_owned(self):
        stop_owned_process(self.process)
        self.process = None
        if self._startup_log is not None:
            self._startup_log.finish()
            self._startup_log = None

    def _release_after_oom(self, process, is_active):
        # Never replay a partly streamed answer (audio may already be playing).
        # The next explicit request may start only our own service with more reserve.
        if process is None or not is_active():
            return False
        with self._startup_lock(is_active):
            if process is not self.process or self._startup_log is None:
                return False
            if process.poll() is not None:
                self._startup_log.finish()
            if not self._startup_log.oom.is_set():
                return False
            self._reserve_floor = _GEMMA_RESERVES[-1]
            self._stop_owned()
            return True

    def stream_answer(self,messages:list[dict[str,str]],lang:str,is_active=lambda:True)->Iterator[str]:
        """SSE-Deltas ohne Wartezeit auf die vollständige LLM-Antwort."""
        if not is_active():return
        if self.prepare_voice is None:
            self.ensure(is_active)
        else:
            self.ensure(is_active,self.prepare_voice)
        if not is_active():return
        prompt=("Du bist OMOI Mini. Antworte kurz, natürlich und hilfreich auf Deutsch." if lang=="de"
                else "Ты OMOI Mini. Отвечай кратко и понятно по-русски.")
        process=self.process
        data={"model":"omoi-managed-chat",
              "messages":[{"role":"system","content":prompt}]+messages[-8:],
              "max_tokens":160,"stream":True,"temperature":0.6}
        try:
            with closing(_sse_events(self.endpoint+"/v1/chat/completions",data,{},
                    is_active,connect=5,trust_env=False)) as events:
                for event in events:
                    if event.get("error"):raise ProviderFailure("Gemma meldet einen Streaming-Fehler")
                    for choice in event.get("choices",[]):
                        part=(choice.get("delta") or {}).get("content")
                        if isinstance(part,str) and part:yield part
        except (ProviderFailure,httpx.HTTPError,ValueError,KeyError,IndexError,TypeError) as exc:
            if is_active():
                if self._release_after_oom(process,is_active):
                    raise ProviderFailure("Gemma: GPU-Speicher erschoepft. Naechste Anfrage startet mit groesserer Reserve.") from None
                if isinstance(exc,ProviderFailure):raise
                raise ProviderFailure("Lokale Gemma konnte nicht streamen") from None

    def answer(self,messages:list[dict[str,str]],lang:str)->str:
        answer="".join(self.stream_answer(messages,lang)).strip()
        if not answer:raise ProviderFailure("Gemma hat keinen sichtbaren Antworttext geliefert")
        return answer
    def unload_if(self,unused):
        with self.lock:
            if not unused():return
            self._stop_owned()

    def close(self):
        self._closed.set()
        self.unload_if(lambda:True)


def transcribe_openai(wav_bytes: bytes, language: str, *, is_active=lambda:True) -> str|None:
    if not is_active():return None
    key=os.environ.get("OPENAI_API_KEY")
    if not key: raise ProviderFailure("OPENAI_API_KEY fehlt")
    if len(wav_bytes)>16*1024*1024: raise ProviderFailure("Aufnahme ist zu groß")
    try:
        response=cancellable_post("https://api.openai.com/v1/audio/transcriptions",
                is_active=is_active,timeout=60,max_bytes=1024*1024,
                headers={"Authorization":"Bearer "+key},
                data={"model":"gpt-transcribe","language":language},
                files={"file":("sprache.wav",wav_bytes,"audio/wav")})
        if response is None:return None
        _check_status(response,"OpenAI STT")
        body=response.json()
        result=body.get("text") if isinstance(body,dict) else None
        result=result.strip() if isinstance(result,str) else ""
        if not result: raise ProviderFailure("Keine Sprache erkannt")
        return result
    except (httpx.HTTPError,ValueError):
        raise ProviderFailure("Spracherkennung ist fehlgeschlagen") from None

def nika_voice_id(configured: str = "") -> str:
    explicit=configured or os.environ.get("CARTESIA_VOICE_ID","").strip()
    if explicit:
        if len(explicit)>160 or any(c.isspace() for c in explicit):
            raise ProviderFailure("Ungültige Cartesia-Voice-ID")
        return explicit
    # Das akzeptierte Entwicklerprofil ist nur im Quell-Checkout verfügbar.
    # Eine portable EXE darf keine private Konfiguration von Haupt-OMOI lesen.
    if not legacy_development_enabled():
        raise ProviderFailure("Bitte Cartesia Voice-ID in den Mini-Einstellungen oder als CARTESIA_VOICE_ID konfigurieren")
    profile_file=omoi_model_root()/"voices"/"profiles.json"
    if not profile_file.is_file(): raise ProviderFailure("Stimmenprofil Nika nicht gefunden")
    try:
        raw=json.loads(profile_file.read_text(encoding="utf-8"))
        profile=next(p for p in raw.get("profiles",[]) if p.get("name")=="Nika")
        binding=next(b for b in profile.get("bindings",[]) if b.get("providerId")=="cartesia")
        voice_id=binding.get("providerVoiceId","")
        if not isinstance(voice_id,str) or not voice_id: raise ValueError()
        return voice_id
    except (ValueError,KeyError,StopIteration,TypeError):
        raise ProviderFailure("Cartesia-Sprachprofil Nika ist unvollständig") from None

def cartesia_voice_status(configured: str = "") -> str:
    """Nur Konfiguration prüfen; keine Netzwerkverbindung, keine ID ausgeben."""
    try:
        nika_voice_id(configured)
    except ProviderFailure:
        return "missing"
    if not os.environ.get("CARTESIA_API_KEY"):
        return "key_missing"
    return "mini" if configured else "environment" if os.environ.get("CARTESIA_VOICE_ID","").strip() else "legacy"


def _normalize_cartesia_wav(data: bytes) -> bytes:
    """Cartesia may stream a WAV with placeholder RIFF/data sizes.
    Rebuild finite PCM WAV for Qt/cache without changing the audio samples."""
    if not 44 <= len(data) <= 32 * 1024 * 1024:
        raise ProviderFailure("Cartesia hat keine gültige WAV-Datei geliefert")
    try:
        with wave.open(io.BytesIO(data),"rb") as source:
            if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=44100:
                raise ValueError("Unexpected PCM format")
            samples=source.readframes(len(data)//2)
            if not samples or len(samples)%2:
                raise ValueError("Empty or incomplete PCM")
        out=io.BytesIO()
        with wave.open(out,"wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(44100)
            target.writeframes(samples)
        return out.getvalue()
    except (wave.Error,EOFError,ValueError,OverflowError):
        raise ProviderFailure("Cartesia hat keine gültige PCM-WAV-Datei geliefert") from None


def cartesia_wav(text: str, language: str, *, voice_id: str = "",is_active=lambda:True) -> bytes|None:
    if not is_active():return None
    key=os.environ.get("CARTESIA_API_KEY")
    if not key: raise ProviderFailure("CARTESIA_API_KEY fehlt")
    if not (0<len(text)<2500): raise ProviderFailure("Ungültiger Text für Sprachausgabe")
    body={"model_id":"sonic-3.6","transcript":text,"voice":nika_voice_id(voice_id),
          "output_format":{"container":"wav","encoding":"pcm_s16le","sample_rate":44100},
          "language":language}
    try:
        response=cancellable_post("https://api.cartesia.ai/tts/bytes",
              is_active=is_active,timeout=50,
              headers={"Authorization":"Bearer "+key,"Cartesia-Version":"2026-08-14"},
              json=body)
        if response is None:return None
        _check_status(response,"Cartesia")
        data=response.content
        return _normalize_cartesia_wav(data)
    except httpx.HTTPError:
        raise ProviderFailure("Cartesia: Verbindung fehlgeschlagen") from None
