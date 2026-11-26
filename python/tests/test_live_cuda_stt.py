"""Fluechtige CUDA-Partial-ASR-Snapshots waehrend PTT, niemals Cloud/LLM."""
from __future__ import annotations

import json
import time
import wave
import io

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.recorder import Recorder


def pump_until(predicate, timeout=2.5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(.012)
    return False


def fake_samples(seconds=2.3):
    t=np.arange(int(16000*seconds))
    return (np.sin(2*np.pi*300*t/16000)*6000).astype(np.int16).reshape(-1,1)


def test_recorder_snapshots_are_complete_wav_and_do_not_consume_frames():
    recorder=Recorder()
    samples=fake_samples()
    recorder._accept_frame(samples[:5000])
    assert recorder.snapshot()==b""
    recorder._accept_frame(samples[5000:])
    first=recorder.snapshot()
    second=recorder.snapshot()
    assert first==second
    with wave.open(io.BytesIO(first),"rb") as wav:
        assert wav.getframerate()==16000
        assert wav.getnchannels()==1
        assert wav.getnframes()==len(samples)
    recorder._accept_frame(fake_samples(0.3))
    assert recorder.snapshot()!=first
    assert recorder._samples==len(samples)+4800


def test_live_partial_cuda_does_not_call_llm_or_cloud(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"live.sqlite3")
    store.set_setting("stt","cuda")
    store.set_setting("speak",False)
    ctrl=Controller(store)
    events=[]
    ctrl.event.connect(lambda data:events.append(json.loads(data)))
    observed=[]
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",
                        lambda wav,lang,**kwargs:observed.append((len(wav),lang)) or "Hallo OMOI Mini")
    monkeypatch.setattr(ctrl.whisper,"transcribe",
                        lambda *args:(_ for _ in ()).throw(AssertionError("OpenVINO")))
    monkeypatch.setattr("omoi_mini.controller.transcribe_openai",
                        lambda *args:(_ for _ in ()).throw(AssertionError("Cloud STT")))
    monkeypatch.setattr(ctrl,"send_text",
                        lambda *args:(_ for _ in ()).throw(AssertionError("LLM before stop")))
    monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
    turn=ctrl.stop()
    recorder=Recorder()
    recorder._accept_frame(fake_samples())
    try:
        assert ctrl.partial_audio(recorder.snapshot(),turn)
        assert pump_until(lambda:any(e.get("type")=="stt_partial" for e in events))
        assert events[-1]["text"]=="Hallo OMOI Mini"
        assert events[-1]["final"] is False
        assert store.messages()==[]
        assert observed[0][1]=="de"
        # Neue Spracheingabe verwirft alte Hypothese automatisch.
        new_turn=ctrl.stop()
        assert new_turn>turn
        assert not ctrl.partial_audio(recorder.snapshot(),turn)
    finally:
        ctrl.close()


def test_partial_only_for_explicit_cuda_stt(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"cloud.sqlite3")
    ctrl=Controller(store)
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",
                        lambda *_:(_ for _ in ()).throw(AssertionError("Cloud mode")))
    try:
        turn=ctrl.stop()
        assert not ctrl.partial_audio(b"RIFF"+"0".encode()*5000,turn)
        store.set_setting("stt","local")
        assert not ctrl.partial_audio(b"RIFF"+"0".encode()*5000,turn)
    finally:
        ctrl.close()


def test_partial_coalesces_when_cuda_is_busy(tmp_path,monkeypatch):
    from threading import Event
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"busy.sqlite3")
    store.set_setting("stt","cuda")
    ctrl=Controller(store)
    started=Event()
    release=Event()
    def slow(*args,**kwargs):
        started.set()
        release.wait(1)
        return "Wort"
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",slow)
    monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
    turn=ctrl.stop()
    recorder=Recorder()
    recorder._accept_frame(fake_samples())
    try:
        assert ctrl.partial_audio(recorder.snapshot(),turn)
        assert started.wait(timeout=1)
        assert not ctrl.partial_audio(recorder.snapshot(),turn)
        release.set()
        assert pump_until(lambda:not ctrl._partial_inflight)
    finally:
        release.set()
        ctrl.close()


class ClosedFakeStream:
    def stop(self):
        pass
    def close(self):
        pass


def test_quiet_microphone_is_no_longer_discarded():
    """Vorher wurde Sprache unter 1,2 % RMS trotz korrektem Signal verworfen."""
    rec=Recorder()
    rec.stream=ClosedFakeStream()
    t=np.arange(16000*2)
    low=np.sin(2*np.pi*280*t/16000)*0.006
    rec._accept_frame((low*32767).astype(np.int16).reshape(-1,1))
    level=rec.signal_level()
    assert level["rms"]>0.0025
    final=rec.stop()
    assert final.startswith(b"RIFF")
    assert rec.last_error==""
    assert rec.last_metrics["voiceMs"]>=100
    assert rec.last_metrics["seconds"]==2.0


def test_silent_microphone_returns_explanatory_reason():
    rec=Recorder()
    rec.stream=ClosedFakeStream()
    rec._accept_frame(np.zeros((32000,1),dtype=np.int16))
    assert rec.stop()==b""
    assert rec.last_error=="MICROPHONE_SIGNAL_TOO_QUIET"
    assert rec.last_metrics["voiceMs"]==0


def test_stt_worker_os_error_is_not_silenced(monkeypatch,tmp_path):
    from omoi_mini.local_stt import LocalWhisper
    from omoi_mini.providers import ProviderFailure
    worker=LocalWhisper(engine="openvino")
    worker.worker=tmp_path/"worker.mjs"
    worker.worker.write_text("test",encoding="utf-8")
    (tmp_path/"node_modules"/"openvino-genai-node").mkdir(parents=True)
    model=tmp_path/"model"
    model.mkdir()
    monkeypatch.setenv("OMOI_MINI_STT_MODEL",str(model))
    def fail_start(*args,**kwargs):
        raise FileNotFoundError("node not installed")
    monkeypatch.setattr("omoi_mini.local_stt.subprocess.Popen",fail_start)
    with pytest.raises(ProviderFailure,match="Node.js-Pfad"):
        worker.ensure()
    assert worker.proc is None


def test_unexpected_stt_thread_error_is_visible(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"unexpected.sqlite3")
    store.set_setting("stt","local")
    store.set_setting("speak",False)
    ctrl=Controller(store)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.whisper,"transcribe",
                        lambda *a,**k:(_ for _ in ()).throw(FileNotFoundError("private-user-path")))
    try:
        turn=ctrl.stop()
        ctrl._transcribe(turn,b"simulated-audio")
        assert any(event.get("type")=="stt_stage" and event.get("stage")=="error"
                   for event in events)
        text=" ".join(event.get("value","") for event in events)
        assert "Spracherkennung fehlgeschlagen" in text
        assert "private-user-path" not in text
    finally:
        ctrl.close()
