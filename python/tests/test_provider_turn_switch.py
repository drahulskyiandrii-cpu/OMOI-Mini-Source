"""Anbieterwechsel, Aufnahmegrenze und Abbruch ohne echte KI."""
import json
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge, RECORDING_SETTINGS_ERROR


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c=Controller(Store(tmp_path/"db"))
    try:yield c
    finally:c.close()


@pytest.mark.parametrize("key,value",[
    ("stt","cuda"),("llm_mode","local_standard"),("llm","local"),
    ("tts","local"),("speak",False),("language","ru"),
    ("qwen_variant","cf050"),("stt_profile","quality"),("stt_review",True),
])
def test_provider_setting_cancels_current_turn(ctrl,key,value):
    turn=ctrl.next_turn()
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    assert ctrl.change_turn_setting(key,value)
    assert not ctrl.current(turn)
    assert ctrl.current(turn+1)
    assert ctrl.store.get_settings()[key]==value
    assert any(e["type"]=="animation_reset" and e["turn"]==turn+1 for e in events)
    assert not ctrl.change_turn_setting(key,value)
    assert ctrl.current(turn+1)


def test_invalid_setting_preserves_turn(ctrl):
    turn=ctrl.next_turn()
    with pytest.raises(ValueError):ctrl.change_turn_setting("stt","invalid")
    assert ctrl.current(turn)
    assert ctrl.store.get_settings()["stt"]=="cloud"


def test_recording_refuses_cloud_switch_at_bridge(ctrl):
    ctrl.store.set_setting("stt","cuda")
    turn=ctrl.next_turn()
    events=[]
    bridge=SimpleNamespace(recording=True,controller=ctrl,
        window=SimpleNamespace(store=ctrl.store),
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    Bridge.saveSetting(bridge,"stt",json.dumps("cloud"))
    assert ctrl.store.get_settings()["stt"]=="cuda"
    assert ctrl.current(turn)
    assert events==[
        {"type":"setting","key":"stt","value":"cuda"},
        {"type":"error","value":RECORDING_SETTINGS_ERROR},
    ]


def test_cancelled_recording_is_not_dispatched(ctrl):
    turn=ctrl.next_turn()
    ctrl.stop()
    calls=[]
    timer=SimpleNamespace(stop=lambda:None)
    bridge=SimpleNamespace(recording=True,_recording_turn=turn,
        controller=ctrl,_stt_partial_timer=timer,_mic_level_timer=timer,
        recorder=SimpleNamespace(stop=lambda:b"private synthetic audio",last_metrics={}),
        uiEvent=SimpleNamespace(emit=lambda raw:calls.append(json.loads(raw))))
    ctrl.listen_audio=lambda *a:pytest.fail("stale recording dispatched")
    Bridge.toggleRecord(bridge)
    assert not bridge.recording
    assert calls[0]=={"type":"recording","active":False}


def test_stt_snapshot_cannot_route_cancelled_audio_to_cloud(ctrl,monkeypatch):
    ctrl.store.set_setting("stt","cuda")
    turn=ctrl.next_turn()
    original=ctrl.store.get_settings
    switched=False
    def get_settings():
        nonlocal switched
        if not switched:
            switched=True
            ctrl.change_turn_setting("stt","cloud")
        return original()
    monkeypatch.setattr(ctrl.store,"get_settings",get_settings)
    monkeypatch.setattr("omoi_mini.controller.transcribe_openai",
        lambda *a,**k:pytest.fail("cancelled local audio sent to cloud"))
    ctrl._transcribe(turn,b"synthetic audio")
    assert not ctrl.current(turn)
    assert ctrl.store.messages()==[]


def test_tts_cancel_after_model_ready_skips_synthesis(ctrl,tmp_path,monkeypatch):
    ctrl.store.set_setting("tts","local")
    turn=ctrl.next_turn()
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:ctrl.change_turn_setting("tts","cartesia"))
    monkeypatch.setattr("omoi_mini.controller.local_tts_wav",
        lambda *a,**k:pytest.fail("cancelled local synthesis"))
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",
        lambda *a,**k:pytest.fail("unexpected cloud synthesis"))
    ctrl._speak("Test","de",turn)
    assert not ctrl.current(turn)
    assert not list(tmp_path.rglob("*.wav"))
