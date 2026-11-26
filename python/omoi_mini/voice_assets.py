"""Persönliche Referenzstimme lokal und ohne TTS-/GPU-Inferenz importieren."""
from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from pathlib import Path

from .core import user_data_dir

MAX_REFERENCE_BYTES = 20 * 1024 * 1024


class InvalidVoice(ValueError):
    pass


def validate_voice_wav(source: str | Path) -> float:
    path = Path(source)
    if (not path.is_file() or path.is_symlink()
            or path.suffix.lower() != ".wav"
            or not 100 <= path.stat().st_size <= MAX_REFERENCE_BYTES):
        raise InvalidVoice("WAV-Datei fehlt oder ist zu groß.")
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or wav.getcomptype() != "NONE"
                    or wav.getframerate() not in (16000, 22050, 24000, 44100, 48000)):
                raise InvalidVoice("Mono-PCM16 WAV mit 16/22,05/24/44,1/48 kHz benötigt.")
            duration = wav.getnframes() / wav.getframerate()
            if not 3 <= duration <= 30:
                raise InvalidVoice("Bitte eine saubere 3–30-Sekunden-Stimmprobe auswählen.")
            if len(wav.readframes(wav.getnframes()))!=wav.getnframes()*2:
                raise InvalidVoice("WAV-Datei unvollstaendig.")
            return duration
    except (OSError, EOFError, RuntimeError, wave.Error):
        raise InvalidVoice("WAV-Format kann nicht gelesen werden.") from None


def _digest(path: Path) -> str:
    # Auch bei waehrend des Imports veraenderten Dateien begrenzt lesen.
    with path.open("rb") as fd:
        data=fd.read(MAX_REFERENCE_BYTES+1)
    if len(data)>MAX_REFERENCE_BYTES:
        raise InvalidVoice("WAV-Datei zu gross.")
    return hashlib.sha256(data).hexdigest()


def import_voice_wav(source: str | Path) -> Path:
    source=Path(source)
    validate_voice_wav(source)
    target_dir=user_data_dir()/"voices"
    target_dir.mkdir(parents=True,exist_ok=True)
    digest=_digest(source)
    target=target_dir/(digest+".wav")
    if not target.exists():
        temporary=None
        try:
            with tempfile.NamedTemporaryFile("wb",dir=target_dir,suffix=".wav",delete=False) as fd:
                temporary=Path(fd.name)
                with source.open("rb") as original:
                    data=original.read(MAX_REFERENCE_BYTES+1)
                if len(data)>MAX_REFERENCE_BYTES or hashlib.sha256(data).hexdigest()!=digest:
                    raise InvalidVoice("Stimmreferenz wurde waehrend des Imports geaendert.")
                fd.write(data)
            validate_voice_wav(temporary)
            os.replace(temporary,target)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    validate_voice_wav(target)
    if _digest(target)!=digest:
        raise InvalidVoice("Gespeicherte Stimmreferenz stimmt nicht mit ihrem Hash ueberein.")
    return target
