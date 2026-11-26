"""Eigene Hotwords und sprachgebundene Korrekturen."""
import json
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.stt_quality import hotwords


def test_custom_hotwords_get_budget_before_generic_terms():
    custom="A"*80+"; "+"B"*80
    terms=hotwords(custom)
    assert terms.startswith("A"*80+", "+"B"*80)
    assert len(terms.encode("utf-8"))<=192


@pytest.mark.parametrize("language",["de","ru","en"])
def test_auto_review_uses_detected_language(tmp_path,monkeypatch,language):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("stt","cuda")
    store.set_setting("stt_profile","balanced")
    store.set_setting("stt_auto_language",True)
    store.set_setting("stt_review",True)
    store.remember_stt_correction("cuda","auto","original","legacy")
    store.remember_stt_correction("cuda",language,"original","corrected")
    ctrl=Controller(store)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def transcribe(*args,**kwargs):
        ctrl.cuda_whisper.last_language=language
        return "original"
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",transcribe)
    try:
        ctrl._transcribe(ctrl.next_turn(),b"synthetic")
        review=next(e for e in events if e["type"]=="stt_review")
        assert review["suggestion"]=="corrected"
        assert ctrl._pending_review[2]==language
        assert store.stt_suggestion("cuda","auto","original")=="legacy"
    finally:ctrl.close()
