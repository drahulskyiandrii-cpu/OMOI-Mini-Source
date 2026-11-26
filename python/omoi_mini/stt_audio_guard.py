"""Port of main OMOI STTAudioSpeechGuard.ts (3cdfc429), no neural models."""
from dataclasses import dataclass
import io
import wave
import numpy as np


@dataclass(frozen=True)
class SpeechPattern:
    speech_like: bool
    active_ms: int
    voiced_ms: int
    longest_run_ms: int
    active_ratio: float
    voiced_ratio: float
    transient_ratio: float


def analyze(samples, sample_rate=16000):
    if sample_rate != 16000:
        raise ValueError("PCM16/16 kHz required")
    data = np.asarray(samples,dtype=np.float32).reshape(-1)
    if len(data)>480000:
        raise ValueError("Audio too long")
    if not np.all(np.isfinite(data)) or np.any(np.abs(data)>1):
        raise ValueError("Invalid PCM samples")
    active_count = voiced_count = transient_count = run = gap = longest = frames = 0
    for offset in range(0,len(data),320):
        frame = data[offset:offset+320]
        rms = float(np.sqrt(np.mean(frame*frame)))
        peak = float(np.max(np.abs(frame)))
        crest = peak/max(rms,1e-6)
        zcr = np.count_nonzero((frame[:-1]<0)!=(frame[1:]<0))/max(1,len(frame)-1)
        active = rms>=.0035 and peak>=.012
        transient = active and peak>=.03 and crest>=12
        voiced = active and not transient and zcr<=.28 and crest<=10
        frames += 1
        if active:
            active_count += 1
            run += 1+gap
            gap=0
        elif run>0 and gap<1:
            gap+=1
        else:
            longest=max(longest,run)
            run=gap=0
        voiced_count += int(voiced)
        transient_count += int(transient)
    longest=max(longest,run)
    active_ms,voiced_ms,longest_ms=active_count*20,voiced_count*20,longest*20
    return SpeechPattern(active_ms>=140 and voiced_ms>=60 and longest_ms>=100,
                         active_ms,voiced_ms,longest_ms,active_count/max(1,frames),
                         voiced_count/max(1,frames),transient_count/max(1,frames))


def analyze_wav(data: bytes) -> SpeechPattern:
    if not 44<=len(data)<=16*1024*1024:
        raise ValueError("Invalid WAV")
    with wave.open(io.BytesIO(data),"rb") as stream:
        if (stream.getnchannels()!=1 or stream.getsampwidth()!=2 or
                stream.getframerate()!=16000 or stream.getnframes()>480000 or
                stream.getcomptype()!="NONE"):
            raise ValueError("Unsupported WAV")
        frames=stream.getnframes()
        raw=stream.readframes(frames)
        if len(raw)!=frames*2:
            raise ValueError("Truncated WAV")
        samples=np.frombuffer(raw,dtype="<i2")
    return analyze(samples.astype(np.float32)/32768.0)
