"""Qt-Wiedergabe von WAV und inkrementellen Qwen-PCM-Frames.

Ein gemeinsamer FIFO-Spielplan hält Sätze in Reihenfolge; frische Antworten
brechen die laufende Ausgabe ab. QAudioSink läuft ausschließlich im GUI-Thread.
"""
from __future__ import annotations

import json
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np
from .pcm_wave import read_pcm_wav
from PySide6.QtCore import QObject, QUrl, Signal, QTimer, Slot
from PySide6.QtMultimedia import (
    QMediaPlayer, QAudioOutput, QAudioSink, QAudioFormat, QMediaDevices, QAudio,
)


class AudioPlayer(QObject):
    event = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(0.75)
        self.audio_samples = np.zeros(1, dtype=np.int16)
        self.rate = 44100
        self._active = False
        self._starting_file=False
        self._file_start_failed=False
        self._latest_turn = 0
        self.queue: deque[tuple[str, str]] = deque()
        self._streams: dict[str, dict] = {}
        self._pcm_token: str | None = None
        self._pcm_sink: QAudioSink | None = None
        self._pcm_device = None
        self._pcm_timer = QTimer(self)
        self._pcm_timer.setInterval(15)
        self._pcm_timer.timeout.connect(self._pump_pcm)
        self.player.positionChanged.connect(self._position)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._on_error)

    def voice_busy(self)->bool:
        return self._active or bool(self.queue) or bool(self._streams)

    def _emit_state(self, speaking: bool):
        self.event.emit(json.dumps({
            "type": "status", "value": "Spricht…" if speaking else "Bereit",
            "avatarState": "talking" if speaking else "idle",
            "source": "audio",
        }))

    @Slot(str)
    @Slot(str, int)
    def play(self, path: str, turn: int | None = None):
        """Vorgefertigte Antwort sofort spielen und alte Aufgaben abbrechen."""
        if turn is not None and turn != self._latest_turn:return
        self.stop(reset_device=self._pcm_token is not None)
        if self._play_now(path) is False:self._play_next()

    @Slot(str)
    @Slot(str, int)
    def enqueue(self, path: str, turn: int | None = None):
        if turn is not None and turn != self._latest_turn:return
        if self._active or self.queue:
            self.queue.append(("file", path))
        else:
            if self._play_now(path) is False:self._play_next()

    def _play_now(self, path: str):
        # Eine bereits geoeffnete, stille PCM-Senke fuer spaetere Ausgabe behalten.
        try:
            self.rate,channels,data=read_pcm_wav(path)
            self.audio_samples=np.frombuffer(data,dtype="<i2")
            if channels==2:
                self.audio_samples=self.audio_samples.reshape(-1,2).mean(axis=1)
            self._starting_file=True
            self._file_start_failed=False
            self._active=False
            try:
                self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
                if not self._file_start_failed:self.player.play()
            finally:self._starting_file=False
            if self._file_start_failed:
                raise RuntimeError("WAV playback start failed")
            self._active=True
            self._emit_state(True)
            return True
        except (OSError, ValueError, EOFError, wave.Error, RuntimeError):
            self.event.emit(json.dumps({
                "type": "error", "value": "Audiodatei ist nicht abspielbar."
            }))
            self._active=False
            self.audio_samples=np.empty(0,dtype=np.int16)
            return False

    @Slot(int)
    def stop_for_turn(self, turn: int):
        """Später eintreffende PCM-Frames eines alten Turns ignorieren."""
        if turn < self._latest_turn:return
        self._latest_turn = turn
        # Den bereits gestarteten PCM-Treiber nur bei laufender Ausgabe neu
        # initialisieren: sonst ginge die Vorwaermung bei jedem Turn verloren.
        had_pcm = self._pcm_token is not None
        self.stop(reset_device=had_pcm)
        # Keine verzoegerte Geraeteoeffnung nach Stop/Schliessen.
        # Die naechste PCM-Wiedergabe oeffnet bei Bedarf selbst.

    def stop(self, reset_device: bool = True):
        was_active = self._active
        self.queue.clear()
        self._streams.clear()
        if reset_device:
            self._close_pcm()
        else:
            self._pcm_token = None
            self._pcm_timer.stop()
        self._active = False
        self.audio_samples=np.empty(0,dtype=np.int16)
        self.player.stop()
        self.player.setSource(QUrl())
        if was_active:
            self.event.emit(json.dumps({"type": "lip", "value": 0}))
            self._emit_state(False)

    def _close_pcm(self):
        self._pcm_timer.stop()
        self._pcm_token = None
        self._pcm_device = None
        if self._pcm_sink is not None:
            self._pcm_sink.stop()
            self._pcm_sink.deleteLater()
            self._pcm_sink = None

    @Slot(str, int, int)
    def begin_pcm_stream(self, token: str, rate: int, turn: int):
        if turn != self._latest_turn or token in self._streams:
            return
        if rate != 24000 or not token or len(self._streams) >= 20:
            return
        self._streams[token] = {
            "pcm": bytearray(), "offset": 0, "rate": rate,
            "finished": False, "created": time.monotonic(), "first_written": False,
        }
        if self._active or self.queue:
            self.queue.append(("pcm", token))
        else:
            self._start_pcm(token)

    @Slot(str, bytes)
    def pcm_chunk(self, token: str, data: bytes):
        entry = self._streams.get(token)
        if entry is None or entry["finished"]:
            return
        if not data or len(data) % 2 or len(data) > 2 * 1024 * 1024:
            self.pcm_abort(token)
            return
        if len(entry["pcm"]) + len(data) > 32 * 1024 * 1024:
            self.pcm_abort(token)
            return
        entry["pcm"].extend(data)
        if token == self._pcm_token:
            self._pump_pcm()

    @Slot(str)
    def pcm_finished(self, token: str):
        entry = self._streams.get(token)
        if entry is not None:
            entry["finished"] = True
            if token == self._pcm_token:
                self._pump_pcm()

    @Slot(str)
    def pcm_abort(self, token: str):
        if token == self._pcm_token:
            self._close_pcm()
            self._active = False
            self._streams.pop(token, None)
            self._play_next()
            return
        self._streams.pop(token, None)
        self.queue = deque(
            item for item in self.queue if item != ("pcm", token)
        )

    def _open_pcm_sink(self, rate: int):
        if self._pcm_sink is not None and self._pcm_device is not None:
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("NO_AUDIO_OUTPUT")
        sink = QAudioSink(device, fmt, self)
        sink.setVolume(self.output.volume())
        sink.setBufferSize(8192)
        io_device = sink.start()
        if io_device is None:
            sink.deleteLater()
            raise RuntimeError("PCM_AUDIO_SINK_START_FAILED")
        self._pcm_sink = sink
        self._pcm_device = io_device

    def _start_pcm(self, token: str):
        entry = self._streams.get(token)
        if entry is None:
            self._play_next()
            return
        entry["last_progress"]=time.monotonic()
        entry["played_us"]=0
        if self._pcm_sink is not None and self._pcm_device is not None:
            # QAudioSink für Folgesätze wiederverwenden: kein Neustart des Treibers.
            self._pcm_token = token
            entry["playback_base_us"] = self._pcm_sink.processedUSecs()
            self._active = True
            self._pcm_timer.start()
            self._emit_state(True)
            self._pump_pcm()
            return
        try:
            self._open_pcm_sink(entry["rate"])
            self._pcm_token = token
            entry["playback_base_us"] = 0
            self._active = True
            self._pcm_timer.start()
            self._emit_state(True)
            self._pump_pcm()
        except (RuntimeError, ValueError):
            self.event.emit(json.dumps({
                "type": "error", "value": "PCM-Audioausgabe ist nicht verfügbar."
            }))
            self._close_pcm()
            self._streams.pop(token, None)
            self._active = False
            self._play_next()

    def _pump_pcm(self):
        token=self._pcm_token
        if token is None:return
        try:self._pump_pcm_data()
        except (RuntimeError,ValueError,OSError):
            self.event.emit(json.dumps({"type":"error","value":"PCM-Audioausgabe fehlgeschlagen."}))
            self.pcm_abort(token)

    def _pump_pcm_data(self):
        token = self._pcm_token
        sink = self._pcm_sink
        entry = self._streams.get(token or "")
        if token is None or sink is None or entry is None or self._pcm_device is None:
            return
        # PySide6 exposes both QAudio.Error and QtAudio.Error; their
        # enum objects compare unequal even when the Qt value is identical.
        error = sink.error()
        if error.value not in (QAudio.Error.NoError.value,QAudio.Error.UnderrunError.value):
            raise RuntimeError("PCM device failure: " + error.name)
        available = len(entry["pcm"]) - entry["offset"]
        free = max(0, int(sink.bytesFree()))
        now=time.monotonic()
        played_us=max(0,sink.processedUSecs()-entry.get("playback_base_us",0))
        if played_us>entry["played_us"]:
            entry["played_us"]=played_us
            entry["last_progress"]=now
        if available >= 2 and free >= 2:
            length = min(available, free, 8192) & ~1
            if length:
                block = bytes(entry["pcm"][entry["offset"]:entry["offset"] + length])
                n = self._pcm_device.write(block)
                if n is None or n<0 or n>length or n%2:
                    raise RuntimeError("PCM write failed")
                if n > 0:
                    entry["offset"] += n
                    entry["last_progress"]=now
                    if not entry["first_written"]:
                        entry["first_written"] = True
                        self.event.emit(json.dumps({
                            "type": "latency", "stage": "pcm_first_write",
                            "elapsedMs": round((time.monotonic() - entry["created"]) * 1000),
                        }))
        # Nur bei ausstehenden Samples auf Geraetefortschritt warten.
        # Ein langsamer Synthese-Server ist kein defektes Audiogeraet.
        if available or entry["finished"]:
            if now-entry["last_progress"]>5:
                raise RuntimeError("PCM device stalled")
        else:
            entry["last_progress"]=now
        if entry["first_written"]:
            pos = max(0, int(played_us * entry["rate"] / 1_000_000))
            raw = entry["pcm"]
            lo = max(0, (pos - int(entry["rate"] * .035)) * 2)
            hi = min(len(raw), (pos + int(entry["rate"] * .035)) * 2)
            frame = np.frombuffer(bytes(raw[lo:hi & ~1]), dtype="<i2").astype(np.float32)
            rms = float(np.sqrt(np.mean((frame / 32768.0) ** 2))) if frame.size else 0.0
            self.event.emit(json.dumps({"type": "lip", "value": min(1.0, rms * 8)}))
        if entry["finished"] and entry["offset"] == len(entry["pcm"]):
            # Nach dem letzten geschriebenen Sample nur den echten Gerätebuffer abwarten.
            drained = (
                sink.bytesFree() >= max(0, sink.bufferSize() - 256)
                or sink.processedUSecs() - entry.get("playback_base_us", 0) >=
                    max(0, len(entry["pcm"]) // 2 / entry["rate"] * 1_000_000 - 30000)
            )
            if drained:
                self._streams.pop(token, None)
                self._pcm_token = None
                self._active = False
                self._pcm_timer.stop()
                self._play_next()

    def _play_next(self):
        while self.queue:
            kind, value = self.queue.popleft()
            if kind == "pcm":
                if value in self._streams:
                    self._start_pcm(value)
                    return
            else:
                if self._play_now(value):return
        self._active = False
        self.audio_samples=np.empty(0,dtype=np.int16)
        self.event.emit(json.dumps({"type": "lip", "value": 0}))
        self._emit_state(False)
        # Audiotreiber im IDLE fuer den naechsten Turn warm halten.

    def _media_status(self, status):
        if self._starting_file or self._pcm_token is not None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._active:
            self._active = False
            self._play_next()

    def _on_error(self, error, message):
        if self._starting_file:
            self._file_start_failed=True
            return
        if self._active and self._pcm_token is None:
            self.event.emit(json.dumps({
                "type": "error", "value": "Audioausgabe fehlgeschlagen."
            }))
            self._active = False
            self._play_next()

    def _position(self, ms: int):
        if not self._active or self._pcm_token is not None:
            return
        index = int(ms * self.rate / 1000)
        lo = max(0, index - int(self.rate * .035))
        hi = min(len(self.audio_samples), index + int(self.rate * .035))
        frame = np.asarray(self.audio_samples[lo:hi], dtype=np.float32)
        rms = float(np.sqrt(np.mean((frame / 32768.0) ** 2))) if frame.size else 0.0
        self.event.emit(json.dumps({"type": "lip", "value": min(1.0, rms * 8.0)}))
