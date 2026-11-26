"""Isolierter Offline-Qwen3-TTS-Dienst für OMOI Mini.

Nur Loopback. Persönliches Referenzaudio und Modelle bleiben im Benutzerprofil.
'Emotion' ist ein Kompatibilitätsfeld, kein belegtes Style-Controlling bei Base.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
import struct
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse
import anyio
from pydantic import BaseModel, ConfigDict, Field

SIZES = {"0.6B": "qwen3-tts-0.6b-base", "1.7B": "qwen3-tts-1.7b-base"}
MODEL_SIZE = os.environ.get("OMOI_MINI_QWEN_SIZE", "1.7B")
if MODEL_SIZE not in SIZES:
    raise RuntimeError("OMOI_MINI_QWEN_SIZE_INVALID")
VARIANT = os.environ.get("OMOI_MINI_QWEN_VARIANT", "base")
if VARIANT not in ("base", "cf050") or (VARIANT=="cf050" and MODEL_SIZE!="1.7B"):
    raise RuntimeError("OMOI_MINI_QWEN_VARIANT_INVALID")
MODEL_ID = SIZES[MODEL_SIZE] + ("-cf050" if VARIANT=="cf050" else "")
CF_SHA = "88a94c0fbe85800d309f94bb9c28d698bf9c86d9494617b6cf4dd996cc13648b"
CF_BASE_REVISION = "fd4b254389122332181a7c3db7f27e918eec64e3"
CF_SIZE = 34908456
PRIVATE = Path(os.environ["LOCALAPPDATA"]) / "OMOI-Mini" / "tts-eval" / "private-voice-reference"
REFERENCE = Path(os.environ.get("OMOI_MINI_QWEN_REFERENCE","")) if os.environ.get("OMOI_MINI_QWEN_REFERENCE") else PRIVATE / "own-voice-reference-part-a.wav"
USER_VOICES = Path(os.environ["LOCALAPPDATA"]) / "OMOI-Mini" / "voices"
app = FastAPI(title="OMOI Mini Qwen Local TTS", docs_url=None, redoc_url=None)
_lock = threading.Lock()
_load_lock = threading.Lock()
_model = None
_state = {"state": "not_loaded", "load_seconds": None, "last_error": None}


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = MODEL_ID
    input: str = Field(min_length=1, max_length=2000)
    voice: Literal["reference"] = "reference"
    language: Literal["de", "ru", "en"] = "de"
    emotion: Literal["neutral", "happy", "amused", "sad", "excited"] = "neutral"
    response_format: Literal["wav", "pcm"] = "wav"
    speed: float | None = Field(default=None, ge=0.8, le=1.3)


def _validate_reference() -> Path:
    path = REFERENCE
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("PRIVATE_VOICE_REFERENCE_MISSING")
    resolved=path.resolve()
    if not (resolved.is_relative_to(PRIVATE.resolve()) or resolved.is_relative_to(USER_VOICES.resolve())):
        raise RuntimeError("PRIVATE_REFERENCE_OUTSIDE_PROFILE")
    return path


def _cf_adapter_bytes(path: Path) -> bytes:
    """Genau geprüfte Bytes lesen; keine Torch-/CUDA-Initialisierung."""
    config=path.with_name("adapter.json")
    if (path.is_symlink() or not path.is_file() or path.stat().st_size!=CF_SIZE
            or config.is_symlink() or not config.is_file() or config.stat().st_size>65536):
        raise RuntimeError("CF050_ADAPTER_MISSING_OR_INVALID")
    payload=path.read_bytes()
    if len(payload)!=CF_SIZE or hashlib.sha256(payload).hexdigest()!=CF_SHA:
        raise RuntimeError("CF050_ADAPTER_HASH_MISMATCH")
    meta=json.loads(config.read_text(encoding="utf-8"))
    if meta.get("r")!=16 or meta.get("alpha")!=32 or meta.get("base_revision")!=CF_BASE_REVISION:
        raise RuntimeError("CF050_ADAPTER_CONFIG_MISMATCH")
    return payload


def _cf_base_snapshot() -> str:
    """Pin the exact revision, but do not require model-card/git metadata."""
    from huggingface_hub import snapshot_download
    required=(
        "config.json", "generation_config.json", "merges.txt",
        "model.safetensors", "preprocessor_config.json",
        "tokenizer_config.json", "vocab.json",
        "speech_tokenizer/config.json", "speech_tokenizer/configuration.json",
        "speech_tokenizer/model.safetensors",
        "speech_tokenizer/preprocessor_config.json",
    )
    # Hub >=1.x may reject a cached snapshot if README/.gitattributes are
    # absent. Explicitly request the files used for this pinned model.
    root=Path(snapshot_download("Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                               revision=CF_BASE_REVISION,
                               local_files_only=True,allow_patterns=list(required)))
    if root.name!=CF_BASE_REVISION or any(
            not (root/name).is_file() or (root/name).stat().st_size==0
            for name in required):
        raise RuntimeError("OFFLINE_MODEL_MISSING")
    return str(root)


def _apply_cf050(model, payload: bytes):
    """50% des LoRA-Deltas vor Graph-Capture, niemals Basisdateien ändern."""
    import torch
    from safetensors.torch import load
    tensors=load(payload)
    prefixes=sorted(k[:-len(".lora_A.weight")] for k in tensors if k.endswith(".lora_A.weight"))
    if len(prefixes)!=196 or len(tensors)!=392:
        raise RuntimeError("CF050_MODULE_SET_MISMATCH")
    params=dict(model.model.model.talker.model.named_parameters())
    for prefix in prefixes:
        a=tensors[prefix+".lora_A.weight"]
        b=tensors.get(prefix+".lora_B.weight")
        p=params.get(prefix+".weight")
        if (b is None or p is None or a.ndim!=2 or b.ndim!=2 or a.shape[0]!=16
                or b.shape[1]!=16 or tuple(p.shape)!=(b.shape[0],a.shape[1])):
            raise RuntimeError("CF050_SHAPE_MISMATCH")
    with torch.inference_mode():
        for prefix in prefixes:
            p=params[prefix+".weight"]
            a=tensors[prefix+".lora_A.weight"].to(p.device,dtype=torch.float32)
            b=tensors[prefix+".lora_B.weight"].to(p.device,dtype=torch.float32)
            # alpha/r=2; strength=0.5 -> exakt 1*(B@A).
            p.copy_((p.float()+b@a).to(p.dtype))
            del a,b
    torch.cuda.synchronize()


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:
            return _model
        _validate_reference()
        _state["state"] = "loading"
        started = time.perf_counter()
        try:
            import torch
            from faster_qwen3_tts import FasterQwen3TTS
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA_UNAVAILABLE")
            source=f"Qwen/Qwen3-TTS-12Hz-{MODEL_SIZE}-Base"
            payload=None
            if VARIANT=="cf050":
                adapter=Path(os.environ.get("OMOI_MINI_QWEN_CF50_ADAPTER") or
                             PRIVATE.parent/"cf-lora"/"adapter.safetensors")
                payload=_cf_adapter_bytes(adapter)
                source=_cf_base_snapshot()
            model = FasterQwen3TTS.from_pretrained(
                source, device="cuda", dtype=torch.bfloat16, attn_implementation="sdpa",
                local_files_only=True,
            )
            if payload is not None:
                _apply_cf050(model,payload)
            model.warmup(prefill_len=100)
            torch.cuda.synchronize()
            _model = model
        except Exception as exc:
            # Nur feste Fehlercodes, keine Pfade, Stacktraces oder privaten Daten.
            name=type(exc).__name__
            if str(exc)=="CUDA_UNAVAILABLE":code="CUDA_UNAVAILABLE"
            elif name=="OutOfMemoryError":code="CUDA_OUT_OF_MEMORY"
            elif str(exc).startswith("CF050_"):code="CF050_ADAPTER_INVALID"
            elif name in ("LocalEntryNotFoundError","IncompleteSnapshotError","FileNotFoundError") or str(exc)=="OFFLINE_MODEL_MISSING":code="OFFLINE_MODEL_MISSING"
            elif isinstance(exc,ImportError):code="DEPENDENCY_MISSING"
            else:code="MODEL_NOT_READY"
            _state["state"] = "error"
            _state["last_error"] = code
            raise RuntimeError(code) from None
        _state["state"] = "ready"
        _state["load_seconds"] = round(time.perf_counter() - started, 3)
        _state["last_error"] = None
        return _model


def _pcm_wav(samples, sample_rate: int) -> bytes:
    import numpy as np
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size < 100 or not np.isfinite(samples).all():
        raise ValueError("INVALID_PCM_SAMPLES")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    result = io.BytesIO()
    with wave.open(result, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return result.getvalue()


def _atempo(wav: bytes, sample_rate: int, speed: float) -> bytes:
    if speed == 1.0:
        return wav
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFMPEG_REQUIRED_FOR_TEMPO")
    cmd = [
        ffmpeg, "-nostdin", "-v", "error", "-f", "wav", "-i", "pipe:0",
        "-filter:a", f"atempo={speed:.3f}", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, input=wav, capture_output=True, timeout=30, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        raise RuntimeError("AUDIO_TEMPO_FAILED") from None
    if len(result.stdout) < 200 or len(result.stdout) % 2:
        raise RuntimeError("INVALID_TEMPO_RESULT")
    data = io.BytesIO()
    with wave.open(data, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(result.stdout)
    return data.getvalue()


@app.get("/health")
def health():
    return {
        "service": "omoi-mini-local-tts",
        "model": MODEL_ID,
        "voice_profile": hashlib.sha256(str(REFERENCE.resolve()).encode("utf-8")).hexdigest()[:12],
        "state": _state["state"],
        "loaded": _model is not None,
        "offline": True,
    }


@app.post("/warmup")
def warmup():
    """Sprecher-Embedding und wirklichen Streaming-Pfad im Hintergrund laden."""
    if _state.get("voice_warmed"):
        return health()
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="GENERATION_IN_PROGRESS")
    try:
        model = _load_model()
        if not _state.get("voice_warmed"):
            import torch
            reference = _validate_reference()
            # Die zwei kurzen Probesätze bleiben nur im GPU-Arbeitsspeicher.
            with torch.inference_mode():
                for language, phrase in (
                    ("German", "Hallo!"),
                    ("Russian", "Привет!"),
                ):
                    for _waveform, _rate, _timing in model.generate_voice_clone_streaming(
                        text=phrase, language=language, ref_audio=str(reference),
                        ref_text="", xvec_only=True, chunk_size=8, max_new_tokens=256,
                    ):
                        pass
            _state["voice_warmed"] = True
        return health()
    except Exception as exc:
        code=str(exc)
        allowed={"CUDA_UNAVAILABLE","CUDA_OUT_OF_MEMORY","PRIVATE_VOICE_REFERENCE_MISSING",
                 "CF050_ADAPTER_INVALID","OFFLINE_MODEL_MISSING","DEPENDENCY_MISSING"}
        if type(exc).__name__=="OutOfMemoryError":code="CUDA_OUT_OF_MEMORY"
        raise HTTPException(status_code=503, detail=code if code in allowed else "LOCAL_QWEN_WARMUP_FAILED") from None
    finally:
        _lock.release()


@app.get("/v1/audio/voices")
def voices():
    return {"data": [{"id": "reference", "name": "Eigene lokale Referenzstimme"}]}


def _stream_pcm_chunks(req: SpeechRequest):
    """Rahmen: uint32 big-endian PCM-Bytes; 0=Ende, FFFFFFFF=Fehler."""
    try:
        reference = _validate_reference()
        model = _load_model()
        import numpy as np
        import torch
        seen = 0
        with torch.inference_mode():
            for waveform, sample_rate, _timing in model.generate_voice_clone_streaming(
                text=req.input,
                language={"de": "German", "ru": "Russian", "en": "English"}[req.language],
                ref_audio=str(reference), ref_text="", xvec_only=True,
                chunk_size=8, max_new_tokens=1024,
            ):
                if int(sample_rate) != 24000:
                    raise RuntimeError("UNSUPPORTED_PCM_RATE")
                audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
                if not audio.size:
                    continue
                if not np.isfinite(audio).all():
                    raise RuntimeError("INVALID_PCM_AUDIO")
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                seen += len(pcm)
                if seen > 32 * 1024 * 1024:
                    raise RuntimeError("AUDIO_LIMIT_EXCEEDED")
                yield struct.pack(">I", len(pcm)) + pcm
        if seen == 0:
            raise RuntimeError("EMPTY_AUDIO_STREAM")
        yield struct.pack(">I", 0)
    except Exception:
        # Der HTTP-Header ist ggf. bereits versendet: explizites Fehler-Frame.
        yield struct.pack(">I", 0xFFFFFFFF)

class _LockedPCMResponse(StreamingResponse):
    """Lock erst beim ASGI-Aufruf halten, nicht beim Erzeugen der Antwort."""

    def __init__(self, chunks, **kwargs):
        self.chunks=chunks
        self.iteration_lock=threading.Lock()
        super().__init__(self,**kwargs)

    def __iter__(self):return self

    def __next__(self):
        with self.iteration_lock:return next(self.chunks)

    def _finish(self):
        # Auch bei Disconnect erst die native laufende Iteration abwarten.
        # Niemals gleichzeitig eine zweite GPU-Synthese freigeben.
        with self.iteration_lock:
            try:self.chunks.close()
            finally:_lock.release()

    async def __call__(self,scope,receive,send):
        if not _lock.acquire(blocking=False):
            await JSONResponse({"detail":"GENERATION_IN_PROGRESS"},status_code=429)(scope,receive,send)
            return
        try:
            await super().__call__(scope,receive,send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._finish)


@app.post("/v1/audio/stream")
def synthesize_stream(req: SpeechRequest):
    if req.model != MODEL_ID:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_MODEL")
    speed = req.speed if req.speed is not None else (
        1.15 if req.language == "ru" and MODEL_SIZE == "0.6B" else 1.0
    )
    if req.response_format != "pcm" or abs(speed - 1.0) > 0.001:
        raise HTTPException(status_code=422, detail="PCM_STREAM_REQUIRES_NATIVE_SPEED")
    return _LockedPCMResponse(
        _stream_pcm_chunks(req),
        media_type="application/x-omoi-pcm-stream",
        headers={
            "X-OMOI-TTS-Model": MODEL_ID,
            "X-OMOI-PCM-Rate": "24000",
            "X-OMOI-PCM-Format": "s16le",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/v1/audio/speech")
def synthesize(req: SpeechRequest):
    if req.response_format != "wav":
        raise HTTPException(status_code=422, detail="WAV_REQUIRED")
    if req.model != MODEL_ID:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_MODEL")
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="GENERATION_IN_PROGRESS")
    try:
        reference = _validate_reference()
        try:
            model = _load_model()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="LOCAL_QWEN_UNAVAILABLE") from None
        import numpy as np
        import torch
        started = time.perf_counter()
        first = None
        chunks = []
        rate = None
        samples_seen=0
        try:
            with torch.inference_mode():
                for waveform, sample_rate, _timing in model.generate_voice_clone_streaming(
                    text=req.input,
                    language={"de": "German", "ru": "Russian", "en": "English"}[req.language],
                    ref_audio=str(reference), ref_text="", xvec_only=True,
                    chunk_size=8, max_new_tokens=1024,
                ):
                    if first is None:
                        first = time.perf_counter()
                    chunk = np.asarray(waveform, dtype=np.float32).reshape(-1)
                    if chunk.size:
                        if int(sample_rate)!=24000 or not np.isfinite(chunk).all():
                            raise RuntimeError("INVALID_PCM_AUDIO")
                        samples_seen+=chunk.size
                        if samples_seen*2>32*1024*1024-44:
                            raise RuntimeError("AUDIO_LIMIT_EXCEEDED")
                        chunks.append(chunk)
                        rate = int(sample_rate)
            if not chunks or rate is None:
                raise RuntimeError("NO_AUDIO")
            output = _pcm_wav(np.concatenate(chunks), rate)
            speed = req.speed if req.speed is not None else (1.15 if req.language == "ru" and MODEL_SIZE == "0.6B" else 1.0)
            output = _atempo(output, rate, speed)
            with wave.open(io.BytesIO(output), "rb") as sample:
                duration = sample.getnframes() / sample.getframerate()
        except Exception:
            raise HTTPException(status_code=500, detail="LOCAL_SYNTHESIS_FAILED") from None
        return Response(
            content=output, media_type="audio/wav",
            headers={
                "X-OMOI-TTS-Model": MODEL_ID,
                "X-OMOI-TTS-Language": req.language,
                "X-OMOI-TTS-Speed": str(speed),
                "X-OMOI-TTS-Synthesis-Ms": str(round((time.perf_counter()-started)*1000)),
                "X-OMOI-TTS-First-Chunk-Ms": str(round((first-started)*1000)) if first else "unknown",
                "X-OMOI-TTS-Audio-Seconds": str(round(duration, 3)),
            },
        )
    finally:
        _lock.release()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("OMOI_MINI_TTS_PORT", "18190"))
    if not 1024 <= port <= 65535:
        raise SystemExit("PORT_OUT_OF_RANGE")
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False,
                log_level="warning", workers=1)
