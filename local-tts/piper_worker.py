"""Schneller, komplett lokaler Piper-TTS-Worker mit strikt begrenztem Stdio-JSON.

Getrennte Python-Umgebung und lokale öffentliche Stimmen. Kein Cloud-Fallback.
Die Audiodateien und Modellgewichte verbleiben im Benutzerprofil.
"""
from __future__ import annotations
import base64
import io
import json
import os
import sys
import wave
from pathlib import Path

from piper import PiperVoice

VOICES={
    "de_DE-eva_k-x_low":"de",
    "de_DE-ramona-low":"de",
    "ru_RU-irina-medium":"ru",
}
root=Path(os.environ.get("OMOI_MINI_PIPER_VOICES",
        str(Path(os.environ["LOCALAPPDATA"])/"OMOI-Mini"/"tts-eval"/"voices")))
loaded={}


def emit(item):
    sys.stdout.write(json.dumps(item,ensure_ascii=False)+"\n")
    sys.stdout.flush()


emit({"event":"ready","service":"omoi-mini-piper-tts"})
for line in sys.stdin:
    identifier=None
    try:
        if len(line)>12000:raise ValueError("REQUEST_TOO_LONG")
        request=json.loads(line)
        identifier=request.get("id")
        text=request.get("text")
        voice=request.get("voice")
        language=request.get("language")
        if (not isinstance(text,str) or not 0<len(text)<=1800
                or voice not in VOICES or VOICES[voice]!=language):
            raise ValueError("INVALID_SPEECH_REQUEST")
        if voice not in loaded:
            path=root/(voice+".onnx")
            if not path.is_file():
                raise FileNotFoundError("VOICE_MODEL_MISSING")
            loaded[voice]=PiperVoice.load(path)
        output=io.BytesIO()
        with wave.open(output,"wb") as writer:
            loaded[voice].synthesize_wav(text,writer)
        binary=output.getvalue()
        if len(binary)>16*1024*1024 or binary[:4]!=b"RIFF":
            raise ValueError("INVALID_AUDIO")
        emit({"id":identifier,"ok":True,"audio":base64.b64encode(binary).decode("ascii")})
    except Exception:
        emit({"id":identifier,"ok":False,"error":"SPEECH_FAILED"})
