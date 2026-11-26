"""Push-to-talk mit sicherer, fluechtiger Momentaufnahme fuer CUDA-Partial-ASR.

Kein Audio wird gespeichert oder uebertragen. Ein Snapshot darf auch waehrend
der Aufnahme verwendet werden; STT berechnet ihn im Hintergrund, nie im
sounddevice-Callback oder im Qt-Hauptthread.
"""
from __future__ import annotations

import io
import hashlib
import json
from collections import Counter
import threading
import wave

import numpy as np
from .stt_endpoint import EnergyEndpointVAD
from .pcm_resample import PCMResampler,SUPPORTED_RATES


class MicrophoneUnavailable(RuntimeError):
    """Gespeichertes Eingabegerät fehlt, ist mehrdeutig oder inkompatibel."""


def _input_devices(sd):
    """Stabile Auswahl nach Name/Host-API statt flüchtigem PortAudio-Index."""
    apis=sd.query_hostapis()
    rows=[]
    for index,device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"])<1:
            continue
        name=str(device["name"])
        api=str(apis[int(device["hostapi"])]["name"])
        identity=json.dumps([api,name,int(device["max_input_channels"])],ensure_ascii=False)
        key=hashlib.sha256(identity.encode("utf-8")).hexdigest()
        rows.append({"id":key,"name":name,"api":api,"index":index})
    counts=Counter(row["id"] for row in rows)
    return [dict(row,available=counts[row["id"]]==1) for row in rows]


def microphone_choices(sd=None):
    """Inventur ohne Öffnen eines Audiostreams; keine Aufnahme/ASR."""
    if sd is None:
        import sounddevice as sd
    rows=_input_devices(sd)
    return [{k:v for k,v in row.items() if k!="index"} for row in rows]


def resolve_microphone(selection,sd):
    if selection=="default":
        return None
    matches=[row for row in _input_devices(sd) if row["id"]==selection]
    if len(matches)!=1 or not matches[0]["available"]:
        raise MicrophoneUnavailable("Gewähltes Mikrofon fehlt oder ist nicht eindeutig. Bitte neu auswählen.")
    return matches[0]["index"]


class Recorder:
    def __init__(self):
        self.stream=None
        self.frames=[]
        self.sample_rate=16000  # STT-Ausgabeformat
        self.capture_rate=16000
        self._resampler=PCMResampler(16000)
        self._converted_cursor=0
        self._converted_frames=[]
        self._overflow=False
        self._samples=0
        self._capture_generation=0
        self._lock=threading.Lock()
        self.last_error=""
        self.last_metrics={}
        self._endpoint=None
        self._endpoint_cursor=0

    def configure_endpoint(self, enabled: bool):
        self._endpoint=EnergyEndpointVAD(threshold=300) if enabled else None
        self._endpoint_cursor=0

    def _convert_frames(self):
        # Nur Consumer (GUI); der Callback haelt keine DSP-Arbeit.
        with self._lock:
            frames=list(self.frames[self._converted_cursor:])
            self._converted_cursor=len(self.frames)
        for frame in frames:
            converted=self._resampler.feed(frame)
            if len(converted):self._converted_frames.append(converted)
        return self._converted_frames

    def endpoint_detected(self)->bool:
        if self._endpoint is None or self._overflow:return False
        frames=self._convert_frames()
        for frame in frames[self._endpoint_cursor:]:
            if self._endpoint.feed(frame.tobytes()):
                self._endpoint_cursor=len(frames)
                return True
        self._endpoint_cursor=len(frames)
        return self._endpoint.endpoint_detected

    def _accept_frame(self, data, generation=None, overflow=False):
        """Echtzeit-Callback: nur begrenzte Kopie und Status im RAM."""
        with self._lock:
            if generation is not None and generation!=self._capture_generation:return
            self._overflow=self._overflow or overflow
            remaining=self.capture_rate*30-self._samples
            if remaining>0 and len(data):
                frame=np.asarray(data[:remaining]).copy()
                self.frames.append(frame)
                self._samples+=len(frame)

    def _reset_conversion(self,rate):
        self.capture_rate=rate
        self._resampler=PCMResampler(rate)
        self._converted_cursor=0
        self._converted_frames=[]
        self._endpoint_cursor=0

    def start(self,microphone_id="default"):
        import sounddevice as sd
        if self.stream is not None:return
        with self._lock:
            self._capture_generation+=1
            generation=self._capture_generation
            self.frames=[]
            self._samples=0
            self._overflow=False
        self._reset_conversion(16000)
        self.last_error=""
        self.last_metrics={}
        self._endpoint_cursor=0
        if self._endpoint is not None:self._endpoint.reset()

        def callback(indata,frames,time,status):
            self._accept_frame(indata,generation,bool(getattr(status,'input_overflow',False)))

        device=resolve_microphone(microphone_id,sd)
        for rate in SUPPORTED_RATES:
            try:
                sd.check_input_settings(device=device,channels=1,dtype="int16",samplerate=rate)
                break
            except Exception:continue
        else:
            raise MicrophoneUnavailable("Mikrofon unterstuetzt keine kompatible 16 kHz/32 kHz/44.1 kHz/48 kHz Mono-Aufnahme.")
        self._reset_conversion(rate)
        try:
            self.stream=sd.InputStream(
                device=device,samplerate=self.capture_rate,channels=1,dtype="int16",
                callback=callback,blocksize=1024,
            )
            self.stream.start()
        except Exception:
            stream=self.stream
            self.stream=None
            try:
                if stream is not None:stream.close()
            finally:
                with self._lock:
                    self._capture_generation+=1
                    self.frames=[]
                    self._samples=0
                self._reset_conversion(16000)
            raise

    @staticmethod
    def _as_wav(raw:bytes, sample_rate:int)->bytes:
        out=io.BytesIO()
        with wave.open(out,"wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(raw)
        return out.getvalue()

    def signal_level(self)->dict[str,float]:
        """Nur Pegel-Statistiken; kein privates Mikrofon-Audio nach aussen."""
        with self._lock:
            count=self._samples
            frames=list(self.frames[-4:])
            overflow=self._overflow
        if not frames:
            return {"seconds":0.0,"rms":0.0,"peak":0.0,"overflow":overflow,"captureRate":self.capture_rate}
        signal=np.concatenate(frames,axis=0).astype(np.float32)/32768.0
        return {
            "seconds":round(count/self.capture_rate,2),
            "overflow":overflow,
            "captureRate":self.capture_rate,
            "rms":round(float(np.sqrt(np.mean(signal**2))),4),
            "peak":round(float(np.max(np.abs(signal))),4),
        }

    def snapshot(self)->bytes:
        """Noch nicht finaler Whisper-Input (ab einer Sekunde)."""
        with self._lock:
            if self._samples<self.capture_rate or self._overflow:return b""
        frames=self._convert_frames()
        raw=np.concatenate(frames).tobytes() if frames else b""
        return self._as_wav(raw,self.sample_rate) if raw else b""

    def stop(self,*,discard=False)->bytes:
        if self.stream is None:
            return b""
        stream=self.stream
        self.stream=None
        with self._lock:
            self._capture_generation+=1
            duration=self._samples/self.capture_rate
        capture_rate=self.capture_rate
        raw=b""
        try:
            try:stream.stop()
            finally:stream.close()
            if not discard and not self._overflow:
                frames=self._convert_frames()
                raw=np.concatenate(frames).tobytes() if frames else b""
        finally:
            with self._lock:
                self.frames=[]
                self._samples=0
            self._reset_conversion(16000)
        self.last_error=""
        self.last_metrics={
            "seconds":round(duration,2),
            "captureRate":capture_rate,"overflow":self._overflow,
            "rms":0.0,"peak":0.0,"voiceMs":0,
        }
        if discard:return b""
        if self._overflow:
            self.last_error="MICROPHONE_INPUT_OVERFLOW"
            return b""
        if len(raw)<3200:
            self.last_error="AUDIO_TOO_SHORT"
            return b""
        signal=np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768.0
        self.last_metrics["rms"]=round(float(np.sqrt(np.mean(signal**2))),4)
        self.last_metrics["peak"]=round(float(np.max(np.abs(signal))),4)
        count=len(signal)//320
        if count:
            windows=signal[:count*320].reshape(count,320)
            loud=np.sqrt(np.mean(windows**2,axis=1))
            active=int(np.count_nonzero(loud>0.0025))
            self.last_metrics["voiceMs"]=active*20
            # Früher war 1,2 % RMS / 160 ms erforderlich; leise Laptop-
            # Mikrofone wurden dadurch als leere Aufnahme verworfen.
            # Die letzte Sprachentscheidung trifft weiterhin Whisper.
            if active<5:
                self.last_error="MICROPHONE_SIGNAL_TOO_QUIET"
                return b""
        return self._as_wav(raw,self.sample_rate)
