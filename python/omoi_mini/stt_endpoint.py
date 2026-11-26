# Port of OMOI Speech Core S2 energy endpoint (21.11.2026).
"""Conservative streaming energy VAD for 16-kHz signed PCM16 mono input.

Dependency-free, opt-in endpoint hint. Not a neural VAD: noise, music,
far-field microphones and accents need separate real-world evaluation.
The caller owns the audio bytes and user privacy.
"""
from __future__ import annotations

import math
import struct

RATE = 16000
FRAME_MS = 20
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2


class EnergyEndpointVAD:
    """Hysteretic voice-onset and trailing silence detector.

    Default gate: >= 160 ms of voiced frames and then >= 500 ms silence.
    A single transient/noise burst must not trigger an endpoint. Chunk sizes
    need not align to 20 ms; trailing fragments are held in RAM.
    """

    def __init__(self, *, threshold: int = 600,
                 min_voice_ms: int = 160, end_silence_ms: int = 500):
        if (type(threshold) is not int or not 100 <= threshold <= 10000
                or type(min_voice_ms) is not int or not 80 <= min_voice_ms <= 2000
                or min_voice_ms % FRAME_MS
                or type(end_silence_ms) is not int or not 200 <= end_silence_ms <= 3000
                or end_silence_ms % FRAME_MS):
            raise ValueError("INVALID_VAD_CONFIGURATION")
        self.threshold = threshold
        self.min_voice_frames = min_voice_ms // FRAME_MS
        self.silence_frames_needed = end_silence_ms // FRAME_MS
        self._carry = bytearray()
        self._voiced_run = 0
        self._silence_run = 0
        self._speech_detected = False
        self._endpoint = False
        self.total_frames = 0
        self.voiced_frames = 0

    @property
    def speech_detected(self) -> bool:
        return self._speech_detected

    @property
    def endpoint_detected(self) -> bool:
        return self._endpoint

    def feed(self, pcm: bytes) -> bool:
        """Return True once an utterance endpoint has been detected."""
        if (not isinstance(pcm, bytes) or not pcm or len(pcm) % 2):
            raise ValueError("INVALID_PCM16_INPUT")
        if self._endpoint:
            return True
        self._carry.extend(pcm)
        while len(self._carry) >= FRAME_BYTES:
            frame = self._carry[:FRAME_BYTES]
            del self._carry[:FRAME_BYTES]
            squared_sum = sum(v * v for (v,) in struct.iter_unpack("<h", frame))
            rms = math.sqrt(squared_sum / (FRAME_BYTES // 2))
            voiced = rms >= self.threshold
            self.total_frames += 1
            if voiced:
                self.voiced_frames += 1
                self._voiced_run += 1
                self._silence_run = 0
                if self._voiced_run >= self.min_voice_frames:
                    self._speech_detected = True
            else:
                self._voiced_run = 0
                if self._speech_detected:
                    self._silence_run += 1
                    if self._silence_run >= self.silence_frames_needed:
                        self._endpoint = True
                        self._carry.clear()
                        break
        return self._endpoint

    def reset(self) -> None:
        self._carry.clear()
        self._voiced_run = 0
        self._silence_run = 0
        self._speech_detected = False
        self._endpoint = False
        self.total_frames = 0
        self.voiced_frames = 0
