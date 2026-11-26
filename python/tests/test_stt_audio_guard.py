import json
import numpy as np
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.recorder import Recorder
from omoi_mini.stt_audio_guard import analyze, analyze_wav
from omoi_mini.stt_endpoint import EnergyEndpointVAD


def tone(ms=300):
    return (np.sin(2*np.pi*200*np.arange(16*ms)/16000)*5000).astype("<i2")


def test_guard_tone_and_silence():
    assert analyze(tone().astype(np.float32)/32768).speech_like
    assert not analyze(np.zeros(16000)).speech_like
    assert not analyze(np.ones(160)*.04).speech_like


def test_guard_transients_and_noise():
    impulses=np.zeros(16000)
    impulses[::320]=.8
    result=analyze(impulses)
    assert not result.speech_like and result.transient_ratio>.9
    alternating=np.tile([-.2,.2],8000)
    assert not analyze(alternating).speech_like


def test_guard_wav_parser():
    wav=Recorder._as_wav(tone().tobytes(),16000)
    assert analyze_wav(wav).speech_like


def test_energy_endpoint_incremental():
    vad=EnergyEndpointVAD(threshold=300)
    assert not vad.feed(np.zeros(8000,dtype="<i2").tobytes())
    assert not vad.feed(tone(100).tobytes())
    assert not vad.feed(np.zeros(8000,dtype="<i2").tobytes())
    assert not vad.feed(tone(200).tobytes())
    assert not vad.feed(np.zeros(7680,dtype="<i2").tobytes())
    assert vad.feed(np.zeros(320,dtype="<i2").tobytes())
    vad.reset()
    assert not vad.endpoint_detected


def test_recorder_endpoint_only_new_frames():
    rec=Recorder()
    rec.configure_endpoint(True)
    rec._accept_frame(tone(200).reshape(-1,1))
    assert not rec.endpoint_detected()
    before=rec._endpoint.total_frames
    assert not rec.endpoint_detected()
    assert rec._endpoint.total_frames==before
    rec._accept_frame(np.zeros((8000,1),dtype="<i2"))
    assert rec.endpoint_detected()
    rec.configure_endpoint(False)
    assert not rec.endpoint_detected()


def test_controller_guard_opt_in(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"guard.sqlite3")
    store.set_setting("stt","cuda")
    ctrl=Controller(store)
    calls=[]
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:calls.append(1) or "Hallo")
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:None)
    silence=Recorder._as_wav(np.zeros(16000,dtype="<i2").tobytes(),16000)
    try:
        store.set_setting("stt_speech_guard",True)
        ctrl._transcribe(ctrl.stop(),silence)
        assert len(calls)==1  # Fast remains unchanged.
        store.set_setting("stt_profile","balanced")
        ctrl._transcribe(ctrl.stop(),silence)
        assert len(calls)==1
        assert any(e["type"]=="stt_speech_guard" and not e["speechLike"] for e in events)
        ctrl._transcribe(ctrl.stop(),Recorder._as_wav(tone().tobytes(),16000))
        assert len(calls)==2
    finally:
        ctrl.close()
