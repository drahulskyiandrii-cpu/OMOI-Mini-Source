"""Aufnahmegrenzen, Partial-Cancel und Cache ohne echte Geraete."""
import json
import threading
import time
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.providers import ProviderFailure
from omoi_mini.recorder import Recorder


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c=Controller(Store(tmp_path/"db"))
    c.store.set_setting("stt","cuda")
    try:yield c
    finally:c.close()


def test_finished_recording_skips_queued_partial_without_restarting_worker(ctrl,monkeypatch):
    jobs=[]
    monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
    monkeypatch.setattr(ctrl.stt_pool,"submit",lambda f,*a:jobs.append((f,a)))
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:pytest.fail("obsolete preview"))
    turn=ctrl.stop()
    assert ctrl.partial_audio(b"a"*32044,turn)
    ctrl.listen_audio(b"final",turn)
    assert ctrl.current(turn)
    f,args=jobs.pop(0);f(*args)
    assert not ctrl._partial_inflight
    assert not ctrl.partial_audio(b"a"*96044,turn)
    assert len(jobs)==1


@pytest.mark.parametrize("finish",[True,False])
def test_running_partial_survives_normal_finish_but_not_cancel(ctrl,monkeypatch,finish):
    started=threading.Event()
    release=threading.Event()
    active=[]
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def decode(*a,**kw):
        started.set()
        release.wait(2)
        active.append(kw["is_active"]())
        return "Vorschau"
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",decode)
    monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
    monkeypatch.setattr(ctrl,"_transcribe",lambda *a:None)
    turn=ctrl.stop()
    assert ctrl.partial_audio(b"a"*32044,turn)
    try:
        assert started.wait(2)
        if finish:ctrl.listen_audio(b"final",turn)
        else:ctrl.stop()
        release.set()
        end=time.monotonic()+2
        while ctrl._partial_inflight and time.monotonic()<end:
            QCoreApplication.processEvents()
            time.sleep(.01)
        assert active==[finish]
        assert not any(e["type"]=="stt_partial" for e in events)
    finally:release.set()


def test_stt_cancel_while_waiting_for_worker_lock():
    worker=LocalWhisper("cuda")
    errors=[]
    stop=threading.Event()
    def run():
        try:worker.transcribe(b"audio","de",is_active=lambda:not stop.is_set())
        except ProviderFailure as exc:errors.append(str(exc))
    with worker.lock:
        t=threading.Thread(target=run,daemon=True)
        t.start()
        stop.set()
        t.join(2)
        assert not t.is_alive()
    assert errors and worker.proc is None


@pytest.mark.parametrize("method",["stop","close"])
def test_recorder_failure_always_discards_audio(method):
    r=Recorder()
    r._accept_frame(np.ones((16000,1),dtype=np.int16))
    def fail():raise OSError("device lost")
    r.stream=SimpleNamespace(stop=fail if method=="stop" else lambda:None,
        close=fail if method=="close" else lambda:None)
    with pytest.raises(OSError):r.stop()
    assert r.stream is None and not r.frames and r._samples==0
    assert r.snapshot()==b""


def test_clear_chat_releases_active_recording(ctrl):
    events=[]
    calls=[]
    timer=SimpleNamespace(stop=lambda:calls.append("timer"))
    bridge=SimpleNamespace(recording=True,controller=ctrl,
        _stt_partial_timer=timer,_mic_level_timer=timer,
        recorder=SimpleNamespace(stop=lambda **kw:calls.append("recorder")),
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    bridge.close_recording=lambda:Bridge.close_recording(bridge)
    turn=ctrl.stop()
    Bridge.clearChat(bridge)
    assert not bridge.recording and not ctrl.current(turn)
    assert calls==["timer","timer","recorder"]
    assert events==[{"type":"recording","active":False}]


def test_recording_deadline_even_if_driver_stops_sending_frames(monkeypatch):
    calls=[]
    bridge=SimpleNamespace(recording=True,_recording_started=10,
        recorder=SimpleNamespace(signal_level=lambda:{"seconds":0,"rms":0,"peak":0}),
        uiEvent=SimpleNamespace(emit=lambda raw:None),
        toggleRecord=lambda:calls.append("finish"))
    monkeypatch.setattr("omoi_mini.gui.time.monotonic",lambda:40)
    Bridge._recording_level(bridge)
    assert calls==["finish"]


def test_cache_write_failure_removes_temporary_file(tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/"out.wav"
    def fail(*a):raise OSError("replace failed")
    monkeypatch.setattr(Path,"replace",fail)
    with pytest.raises(OSError):Controller._write_cache(path,b"synthetic")
    assert not list(tmp_path.iterdir())


def test_cancelled_tts_does_not_write_cache(ctrl,tmp_path,monkeypatch):
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    turn=ctrl.stop()
    def wav(*a,**k):
        ctrl.stop()
        return b"synthetic"
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",wav)
    ctrl._speak("Text","de",turn)
    assert not list(tmp_path.rglob("*.wav"))

@pytest.mark.parametrize("failure",["device","write","stall","underrun"])
def test_pcm_fault_cannot_hold_audio_queue(monkeypatch,failure):
    from omoi_mini.audio import AudioPlayer,QAudio
    events=[]
    aborted=[]
    entry={"pcm":bytearray(b"\x00\x00"*100),"offset":0,"rate":24000,
        "finished":False,"first_written":False,"last_progress":0 if failure=="stall" else 10,
        "played_us":0,"created":10}
    sink=SimpleNamespace(error=lambda:QAudio.Error.IOError if failure=="device" else QAudio.Error.UnderrunError,
        bytesFree=lambda:0 if failure in ("stall","underrun") else 200,
        processedUSecs=lambda:0)
    player=SimpleNamespace(_pcm_token="test",_pcm_sink=sink,_streams={"test":entry},
        _pcm_device=SimpleNamespace(write=lambda b:-1),
        event=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))),
        pcm_abort=aborted.append)
    player._pump_pcm_data=lambda:AudioPlayer._pump_pcm_data(player)
    monkeypatch.setattr("omoi_mini.audio.time.monotonic",lambda:10)
    AudioPlayer._pump_pcm(player)
    if failure=="underrun":
        assert aborted==[] and events==[]
    else:
        assert aborted==["test"] and events[0]["type"]=="error"


def test_one_failed_runtime_shutdown_does_not_skip_others(ctrl,monkeypatch):
    called=[]
    def broken():raise OSError("synthetic")
    monkeypatch.setattr(ctrl.gemma,"close",broken)
    monkeypatch.setattr(ctrl.whisper,"shutdown",lambda:called.append("cpu"))
    monkeypatch.setattr(ctrl.cuda_whisper,"shutdown",lambda:called.append("cuda"))
    monkeypatch.setattr(ctrl.local_voice,"close",lambda:called.append("qwen"))
    monkeypatch.setattr(ctrl.piper_voice,"shutdown",lambda:called.append("piper"))
    ctrl.close()
    ctrl.close()
    assert called==["cpu","cuda","qwen","piper"]
    assert ctrl.pool._shutdown and ctrl.stt_pool._shutdown and ctrl.tts_pool._shutdown


def test_real_qt_audio_enum_does_not_abort_healthy_pcm(monkeypatch):
    from PySide6.QtMultimedia import QtAudio
    from omoi_mini.audio import AudioPlayer
    entry={"pcm":bytearray(bytes(200)),"offset":0,"rate":24000,
           "finished":False,"first_written":False,"last_progress":10,
           "played_us":0,"created":10}
    events=[]
    sink=SimpleNamespace(error=lambda:QtAudio.Error.NoError,
                         bytesFree=lambda:200,processedUSecs=lambda:0)
    player=SimpleNamespace(_pcm_token="test",_pcm_sink=sink,
        _streams={"test":entry},_pcm_device=SimpleNamespace(write=len),
        event=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    monkeypatch.setattr("omoi_mini.audio.time.monotonic",lambda:10)
    AudioPlayer._pump_pcm_data(player)
    assert entry["offset"]==200 and entry["first_written"]
    assert any(e.get("stage")=="pcm_first_write" for e in events)
