"""Fehler invalidieren Satz-Audio, nicht spaetere Turns."""
import json
import sqlite3
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge
from omoi_mini.providers import ProviderFailure
from omoi_mini.voice_assets import import_voice_wav,InvalidVoice
from omoi_mini.recorder import Recorder


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c=Controller(Store(tmp_path/"db"))
    try:yield c
    finally:c.close()


def test_llm_failure_invalidates_all_pending_tts(ctrl,monkeypatch):
    tasks,events,stops=[],[],[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    ctrl.stop_audio.connect(stops.append)
    monkeypatch.setattr(ctrl.tts_pool,"submit",tasks.append)
    monkeypatch.setattr(ctrl,"_speak",lambda *a,**kw:pytest.fail("stale TTS"))
    def stream(*args):
        yield "Ein ausreichend langer erster Satz fuer die Sprachausgabe. "
        raise ProviderFailure("synthetic disconnect")
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",stream)
    turn=ctrl.next_turn()
    ctrl.store.add_message("user","Test")
    ctrl._process(turn,"Test")
    assert tasks and not ctrl.current(turn) and stops[-1]==turn+1
    assert not ctrl._pending_voice and not ctrl.voice_busy()
    for task in tasks:task()
    assert ctrl.store.messages()==[{"role":"user","content":"Test"}]
    assert any(e["type"]=="error" and e.get("turn")==turn+1 for e in events)


def test_old_llm_error_does_not_cancel_fresh_request(ctrl):
    turn=ctrl.next_turn()
    fresh=ctrl.next_turn()
    ctrl._fail_response(turn,"late")
    assert ctrl.current(fresh)


def test_prepare_phrases_submit_failure_returns_idle(ctrl,monkeypatch):
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def fail(*a,**kw):raise RuntimeError("executor closed")
    monkeypatch.setattr(ctrl.tts_pool,"submit",fail)
    ctrl.prepare_phrases()
    assert not ctrl.voice_busy()
    assert events[-1]["type"]=="status" and events[-1]["avatarState"]=="idle"


def test_stack_signature_tracks_local_profile(ctrl):
    original=ctrl.store.get_settings()
    changed=dict(original,llm_local_profile="gemma12")
    assert ctrl._stack_signature(original)!=ctrl._stack_signature(changed)


@pytest.mark.parametrize("slot,args",[("refreshMicrophones",()),("chooseRuntimePath",("runtime_root",)),
                                      ("readLLMStatus",()),("saveSetting",("speak","false"))])
def test_bridge_locked_db_errors_are_visible(ctrl,monkeypatch,slot,args):
    events=[]
    bridge=SimpleNamespace(window=SimpleNamespace(store=ctrl.store),controller=ctrl,
        recording=False,uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    bridge._llm_status=lambda:Bridge._llm_status(bridge)
    def fail():raise sqlite3.OperationalError("private path")
    monkeypatch.setattr(ctrl.store,"get_settings",fail)
    getattr(Bridge,slot)(bridge,*args)
    assert events and "private path" not in str(events)


def test_imported_voice_cannot_reuse_mismatched_content(tmp_path,monkeypatch):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"data"))
    ref=tmp_path/"voice.wav"
    ref.write_bytes(Recorder._as_wav(bytes(16000*2*4),16000))
    target=import_voice_wav(ref)
    altered=Recorder._as_wav(bytes([1,0])*16000*4,16000)
    target.write_bytes(altered)
    with pytest.raises(InvalidVoice,match="Hash"):import_voice_wav(ref)
    assert target.read_bytes()==altered  # nicht automatisch ueberschreiben


def test_truncated_voice_reference_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"data"))
    ref=tmp_path/"voice.wav"
    ref.write_bytes(Recorder._as_wav(bytes(16000*2*4),16000)[:-2])
    with pytest.raises(InvalidVoice,match="unvollstaendig"):import_voice_wav(ref)
    assert not (tmp_path/"data").exists()
