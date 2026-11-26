import json
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller


def test_corrections_exact_scope_and_delete(tmp_path):
    db=tmp_path/"review.sqlite3"
    store=Store(db)
    assert store.get_settings()["stt_review"] is False
    store.remember_stt_correction("cuda","de","Whispel","Whisper")
    store=Store(db)
    assert store.stt_suggestion("cuda","de","Whispel")=="Whisper"
    assert store.stt_suggestion("local","de","Whispel") is None
    assert store.stt_suggestion("cuda","ru","Whispel") is None
    assert store.stt_suggestion("cuda","de","Whispel Medium") is None
    with pytest.raises(ValueError):
        store.remember_stt_correction("cloud","de","a","b")
    with pytest.raises(ValueError):
        store.remember_stt_correction("cuda","de","a","a")
    store.clear_stt_corrections()
    assert store.stt_suggestion("cuda","de","Whispel") is None


def test_review_does_not_dispatch_until_approved(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"review.sqlite3")
    store.set_setting("stt","cuda")
    store.set_setting("stt_review",True)
    ctrl=Controller(store)
    events, dispatched=[],[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:"Whispel")
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:dispatched.append(a))
    try:
        turn=ctrl.stop()
        ctrl._transcribe(turn,b"synthetic")
        assert not dispatched and not store.messages()
        assert any(e["type"]=="stt_review" and e["suggestion"]=="Whispel" for e in events)
        assert not ctrl.submit_stt_review(turn,"",True)
        assert ctrl.submit_stt_review(turn,"Whisper",True)
        assert len(dispatched)==1 and dispatched[0][1:]==("Whisper","microphone")
        assert not ctrl.submit_stt_review(turn,"Other",True)
        assert store.stt_suggestion("cuda","de","Whispel")=="Whisper"
        turn=ctrl.stop()
        ctrl._transcribe(turn,b"synthetic")
        assert [e for e in events if e["type"]=="stt_review"][-1]["suggestion"]=="Whisper"
        assert len(dispatched)==1
        assert ctrl.cancel_stt_review(turn)
        assert not ctrl.submit_stt_review(turn,"Whisper",False)
    finally:
        ctrl.close()


def test_review_stop_and_unremembered_text(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"review.sqlite3")
    ctrl=Controller(store)
    calls=[]
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:calls.append(a))
    try:
        turn=ctrl.stop()
        ctrl._show_stt_review(turn,"cuda","ru","original")
        ctrl.stop()
        assert not ctrl.submit_stt_review(turn,"corrected",True)
        assert not calls and store.stt_suggestion("cuda","ru","original") is None
        turn=ctrl.stop()
        ctrl._show_stt_review(turn,"cuda","ru","original")
        assert ctrl.submit_stt_review(turn,"corrected",False)
        assert store.stt_suggestion("cuda","ru","original") is None
        assert len(calls)==1
    finally:
        ctrl.close()
