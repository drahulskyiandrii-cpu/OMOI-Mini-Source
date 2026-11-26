"""Isolated local Faster-Whisper CUDA worker. Never downloads or logs audio/text.

Protocol mirrors local-stt/worker.mjs: one JSON request per stdin line,
{"id": integer, "audio": base64 16k mono PCM16 WAV, "language": "de"/"ru"}.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

PROFILE=Path(os.environ["LOCALAPPDATA"]) / "OMOI-Mini" / "stt-eval"
OVERLAY=Path(os.environ.get("OMOI_MINI_CUDA_STT_OVERLAY") or PROFILE / "faster-whisper-overlay")
MODEL=Path(os.environ.get("OMOI_MINI_CUDA_STT_MODEL") or PROFILE / "faster-whisper-small-ct2")
sys.path.insert(0, str(OVERLAY))
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["TRANSFORMERS_OFFLINE"]="1"


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=True, separators=(",",":"))+"\n")
    sys.stdout.flush()


def samples_from_wav(encoded: str) -> np.ndarray:
    raw=base64.b64decode(encoded,validate=True)
    if not 44 <= len(raw) <= 16*1024*1024:
        raise ValueError("INVALID_WAV_LENGTH")
    with wave.open(io.BytesIO(raw),"rb") as wav:
        if (wav.getnchannels()!=1 or wav.getsampwidth()!=2
            or wav.getframerate()!=16000 or wav.getcomptype()!="NONE"):
            raise ValueError("UNSUPPORTED_PCM_FORMAT")
        frames=wav.getnframes()
        if not 1600 <= frames <= 16000*30:
            raise ValueError("INVALID_AUDIO_DURATION")
        buffer=wav.readframes(frames)
        if len(buffer)!=frames*2:
            raise ValueError("TRUNCATED_PCM")
    return np.frombuffer(buffer,dtype="<i2").astype(np.float32)/32768.0


def choose_language(model, samples, requested, preferred):
    """Prefer the selected language on short/uncertain automatic requests."""
    if requested!="auto":
        return requested
    if preferred is None:
        # Legacy protocol: older clients did not supply a preferred language.
        return None
    if len(samples)<5*16000:
        return preferred
    detected,confidence,probabilities=model.detect_language(audio=samples)
    if detected not in ("de","ru","en") or detected==preferred:
        return preferred
    other=max((p for lang,p in probabilities if lang!=detected),default=0.0)
    # Conservative switch: do not let uncertain phonetic guesses override
    # the user's selected language. Thresholds are initial policy, not WER-tested.
    if confidence>=0.85 and confidence-other>=0.20:
        return detected
    return preferred


def uncertain_segments(segments):
    # Main OMOI's conservative joint confidence/no-speech and repetition checks.
    for segment in segments:
        low=getattr(segment,"avg_logprob",None)
        silent=getattr(segment,"no_speech_prob",None)
        ratio=getattr(segment,"compression_ratio",None)
        if isinstance(low,(int,float)) and low < -1.0:
            if isinstance(silent,(int,float)) and silent > 0.6:return True
            if isinstance(ratio,(int,float)) and ratio > 2.4:return True
        if isinstance(ratio,(int,float)) and ratio >= 8:return True
    return False


def startup():
    started=time.perf_counter()
    if not (MODEL/"model.bin").is_file():
        raise RuntimeError("LOCAL_CT2_MODEL_MISSING")
    import torch
    from faster_whisper import WhisperModel
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    model=WhisperModel(str(MODEL),device="cuda",device_index=0,
                       compute_type="float16",num_workers=1,cpu_threads=4)
    if not model.model.is_multilingual:
        raise RuntimeError("MULTILINGUAL_WHISPER_REQUIRED")
    # Trigger CUDA kernels before reporting readiness. No real speech needed.
    silence=np.zeros(16000,dtype=np.float32)
    segments,_=model.transcribe(silence,language="de",beam_size=1,
                                temperature=0,condition_on_previous_text=False,
                                vad_filter=False)
    list(segments)
    return model,round((time.perf_counter()-started)*1000)


def main() -> None:
    try:
        model,load_ms=startup()
        emit({"event":"ready","engine":"faster-whisper-cuda-small","loadMs":load_ms})
    except Exception:
        emit({"event":"fatal","error":"CUDA_STT_RUNTIME_NOT_READY"})
        raise SystemExit(2)
    for line in sys.stdin:
        req_id=None
        try:
            if len(line)>24*1024*1024:
                raise ValueError("REQUEST_TOO_LARGE")
            request=json.loads(line)
            req_id=request.get("id")
            if type(req_id) is not int or not 0<=req_id<=9007199254740991:
                raise ValueError("INVALID_REQUEST_ID")
            language=request.get("language")
            if language not in ("ru","de","auto"):
                raise ValueError("UNSUPPORTED_LANGUAGE")
            if not isinstance(request.get("audio"),str):
                raise ValueError("INVALID_AUDIO")
            samples=samples_from_wav(request["audio"])
            started=time.perf_counter()
            options=request.get("options",{})
            if not isinstance(options,dict) or set(options)-{"beam_size","hotwords","vad_filter","preferred_language"}:
                raise ValueError("INVALID_DECODER_OPTIONS")
            beam=options.get("beam_size",1)
            terms=options.get("hotwords","")
            vad=options.get("vad_filter",False)
            preferred=options.get("preferred_language")
            if preferred not in (None,"de","ru"):
                raise ValueError("INVALID_PREFERRED_LANGUAGE")
            if (type(beam) is not int or beam not in (1,5) or
                    not isinstance(terms,str) or len(terms.encode("utf-8"))>192 or type(vad) is not bool):
                raise ValueError("INVALID_DECODER_OPTIONS")
            kwargs={"hotwords":terms} if terms else {}
            chosen=choose_language(model,samples,language,preferred)
            segments,info=model.transcribe(
                samples,language=chosen,task="transcribe",beam_size=beam,
                temperature=0,condition_on_previous_text=False,
                vad_filter=vad,**kwargs,
            )
            segments=list(segments)
            text=" ".join(segment.text for segment in segments).strip()
            if chosen is not None and getattr(info,"language",None)!=chosen:
                raise ValueError("UNEXPECTED_RECOGNITION_LANGUAGE")
            emit({"id":req_id,"ok":True,"text":text,"language":getattr(info,"language",None),
                  "uncertain":uncertain_segments(segments),
                  "wallMs":round((time.perf_counter()-started)*1000)})
        except Exception:
            emit({"id":req_id,"ok":False,"error":"LOCAL_ASR_FAILED"})


if __name__=="__main__":
    main()
