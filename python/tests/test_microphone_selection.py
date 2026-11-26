"""Persistente Mikrofonwahl ohne echte Aufnahme oder Modellinferenz."""
import json
import sys
from types import SimpleNamespace

import pytest

from omoi_mini import gui, recorder
from omoi_mini.core import Store


def devices():
    return [
        {"name":"Speakers","hostapi":0,"max_input_channels":0},
        {"name":"USB Mic","hostapi":0,"max_input_channels":1},
        {"name":"Built-in Mic","hostapi":1,"max_input_channels":2},
    ]


def mock_sd(rows):
    return SimpleNamespace(query_devices=lambda:rows,
        query_hostapis=lambda:[{"name":"MME"},{"name":"WASAPI"}])


def test_inventory_only_inputs_and_index_independent_id():
    rows=devices()
    sd=mock_sd(rows)
    choices=recorder.microphone_choices(sd)
    assert len(choices)==2
    usb=choices[0]["id"]
    assert "index" not in choices[0]
    assert recorder.resolve_microphone(usb,sd)==1
    rows.reverse()
    assert recorder.resolve_microphone(usb,sd)==1
    rows.pop()
    assert recorder.resolve_microphone(usb,sd)==1
    rows.pop(0)
    assert recorder.resolve_microphone(usb,sd)==0
    rows.clear()
    with pytest.raises(recorder.MicrophoneUnavailable):
        recorder.resolve_microphone(usb,sd)
    assert recorder.resolve_microphone("default",sd) is None


def test_ambiguous_devices_are_not_silently_selected():
    rows=devices()
    rows.append(dict(rows[1]))
    sd=mock_sd(rows)
    choices=recorder.microphone_choices(sd)
    usb=choices[0]
    assert usb["available"] is False
    with pytest.raises(recorder.MicrophoneUnavailable):
        recorder.resolve_microphone(usb["id"],sd)


def test_recorder_opens_selected_device_and_preserves_pcm_contract(monkeypatch):
    sd=mock_sd(devices())
    seen=[]
    sd.check_input_settings=lambda **kw:seen.append(("check",kw))
    sd.InputStream=lambda **kw:seen.append(("stream",kw)) or SimpleNamespace(
        start=lambda:None, stop=lambda:None, close=lambda:None)
    monkeypatch.setitem(sys.modules,"sounddevice",sd)
    r=recorder.Recorder()
    r.start(recorder.microphone_choices(sd)[1]["id"])
    assert seen[0][1]["device"]==seen[1][1]["device"]==2
    assert seen[1][1]["samplerate"]==16000
    assert seen[1][1]["channels"]==1
    assert seen[1][1]["dtype"]=="int16"
    r.stop()
    r.start()
    assert seen[-1][1]["device"] is None
    r.stop()


def test_missing_or_unsupported_mic_never_opens_fallback(monkeypatch):
    sd=mock_sd(devices())
    sd.InputStream=lambda **kw:pytest.fail("Must not open any stream")
    def fail(**kw):
        raise OSError("unsupported")
    sd.check_input_settings=fail
    monkeypatch.setitem(sys.modules,"sounddevice",sd)
    r=recorder.Recorder()
    with pytest.raises(recorder.MicrophoneUnavailable):
        r.start("f"*64)
    with pytest.raises(recorder.MicrophoneUnavailable,match="16 kHz"):
        r.start(recorder.microphone_choices(sd)[0]["id"])
    assert r.stream is None


def test_bridge_persists_selection_and_disallows_change_while_recording(tmp_path,monkeypatch):
    store=Store(tmp_path/"db")
    rows=recorder.microphone_choices(mock_sd(devices()))
    monkeypatch.setattr(gui,"microphone_choices",lambda:rows)
    events=[]
    bridge=SimpleNamespace(window=SimpleNamespace(store=store),recording=False,
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    bridge.refreshMicrophones=lambda:gui.Bridge.refreshMicrophones(bridge)
    assert store.get_settings()["microphone_id"]=="default"
    gui.Bridge.chooseMicrophone(bridge,rows[0]["id"])
    assert Store(store.path).get_settings()["microphone_id"]==rows[0]["id"]
    assert events[-1]["selected"]==rows[0]["id"]
    bridge.recording=True
    gui.Bridge.chooseMicrophone(bridge,"default")
    assert store.get_settings()["microphone_id"]==rows[0]["id"]
    bridge.recording=False
    gui.Bridge.chooseMicrophone(bridge,"a"*64)
    assert store.get_settings()["microphone_id"]==rows[0]["id"]
    gui.Bridge.chooseMicrophone(bridge,"default")
    assert store.get_settings()["microphone_id"]=="default"
    with pytest.raises(ValueError):
        store.set_setting("microphone_id",3)
