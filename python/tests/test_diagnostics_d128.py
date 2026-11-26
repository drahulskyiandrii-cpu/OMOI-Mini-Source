"""Konsistente Partials, STT-Zeiten und Permission-Grenzen."""
import io
import json
import queue
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge,MiniPage
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.providers import ProviderFailure
from omoi_mini.stt_quality import PartialStabilizer


def test_partial_punctuation_and_case_never_mix_old_hypotheses():
    stabilizer=PartialStabilizer()
    for text in ("hallo Welt", "Hallo Welt, heute", "HALLO Welt heute!",
                 "Hallo, Welt heute wieder", "Hallo Welt", "Guten Morgen"):
        stable,tail=stabilizer.update(text)
        assert stable+tail==text


def test_skipped_partial_does_not_construct_wav(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db");store.set_setting("stt","cuda")
    ctrl=Controller(store)
    try:
        turn=ctrl.next_turn()
        bridge=SimpleNamespace(recording=True,controller=ctrl,_recording_turn=turn,
            window=SimpleNamespace(store=store),
            recorder=SimpleNamespace(snapshot=lambda:pytest.fail("unnecessary WAV")))
        monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:False)
        Bridge._recording_partial(bridge)
        monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
        ctrl._partial_inflight=True
        Bridge._recording_partial(bridge)
        ctrl._partial_inflight=False
        ctrl._partial_closed_turn=turn
        Bridge._recording_partial(bridge)
    finally:ctrl.close()


def test_valid_local_timing_reaches_controller(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db");store.set_setting("stt","cuda")
    ctrl=Controller(store)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def fake(*a,**kw):
        ctrl.cuda_whisper.last_decode_ms=123
        ctrl.cuda_whisper.last_load_ms=456
        return "Hallo"
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",fake)
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:None)
    try:
        ctrl._transcribe(ctrl.next_turn(),b"synthetic")
        event=next(e for e in events if e["type"]=="stt_stage" and e["stage"]=="complete")
        assert event["decodeMs"]==123 and event["loadMs"]==456
    finally:ctrl.close()


@pytest.mark.parametrize("value,expected",[(0,0),(130,130),(-1,None),(True,None),(180001,None),("1",None)])
def test_timing_validation(value,expected):
    assert LocalWhisper._valid_ms(value)==expected


def test_request_identifier_boolean_is_not_integer(monkeypatch):
    worker=LocalWhisper("cuda")
    messages=queue.Queue()
    messages.put({"id":True,"ok":True})
    closed=[]
    monkeypatch.setattr(worker,"close",lambda:closed.append(True))
    with pytest.raises(ProviderFailure,match="unterbrochen"):
        worker._request(SimpleNamespace(stdin=io.StringIO()),messages,{"id":1},1)
    assert closed==[True]


def test_qt_browser_permissions_denied_without_opening_gui():
    denied=[]
    MiniPage._deny_permission(SimpleNamespace(deny=lambda:denied.append(True)))
    assert denied==[True]
