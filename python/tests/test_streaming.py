"""SSE-Texte, gleichzeitige TTS-Aufträge und Abbruch ohne Cloudkosten."""
from __future__ import annotations
import json
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.streaming import SpeechChunker


def wait_for(predicate,seconds=2):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        QCoreApplication.processEvents()
        if predicate():return True
        time.sleep(.01)
    return False


def test_chunker_preserves_all_words_and_early_sentences():
    original=("Das erste Beispiel beschreibt ein VLAN und ist ausreichend lang. "
              "Jetzt kommt die zweite Aussage mit weiteren Erläuterungen. "
              "Das war die Antwort.")
    chunks=SpeechChunker()
    emitted=[]
    for ch in [original[:13],original[13:69],original[69:100],original[100:]]:
        emitted.extend(chunks.feed(ch))
    emitted.extend(chunks.finish())
    assert len(emitted)>=2
    assert " ".join(emitted)==original


def test_streamed_ui_text_is_persisted_once(tmp_path:Path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"stream.sqlite3")
    store.set_setting("llm","local")
    store.set_setting("speak",False)
    ctrl=Controller(store)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    sentence="Ein VLAN ist ein virtuelles Netzwerk. " + "Dadurch können Geräte logisch getrennt werden."
    monkeypatch.setattr(ctrl.gemma,"stream_answer",lambda *_:iter([sentence[:16],sentence[16:57],sentence[57:]]))
    try:
        ctrl.send_text("Erkläre mir ein VLAN.")
        assert wait_for(lambda:len(store.messages())==2)
        assert store.messages()[-1]["content"]==sentence
        assert wait_for(lambda:sum(e["type"]=="stream_delta" for e in events)==3)
        assert sum(e["type"]=="stream_start" for e in events)==1
        assert wait_for(lambda:sum(e["type"]=="stream_end" for e in events)==1)
        assert next(e for e in events if e["type"]=="stream_end")["text"]==sentence
        assert not any(e["type"]=="message" and e.get("role")=="assistant" for e in events)
    finally:
        ctrl.close()


def test_speech_starts_before_llm_finishes(tmp_path:Path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"parallel.sqlite3")
    store.set_setting("llm","local")
    store.set_setting("tts","local")
    store.set_setting("speak",True)
    ctrl=Controller(store)
    timeline=[]
    first=("Ein VLAN trennt Geräte logisch voneinander und hilft bei der "
           "Verwaltung. ")
    second=("Die Verbindung zwischen Switches verwendet häufig "
            "einen Trunk-Port.")
    def streamed(*_):
        timeline.append("llm-start")
        yield first
        time.sleep(.15)
        timeline.append("llm-rest")
        yield second
    monkeypatch.setattr(ctrl.gemma,"stream_answer",streamed)
    monkeypatch.setattr(ctrl,"_speak",lambda *a,**k:timeline.append("tts"))
    try:
        ctrl.send_text("Wie funktioniert VLAN?")
        assert wait_for(lambda:"llm-rest" in timeline and len(store.messages())==2)
        assert timeline.index("tts")<timeline.index("llm-rest")
        assert store.messages()[-1]["content"]==first+second
    finally:
        ctrl.close()


def test_stopping_turn_aborts_stream_and_pending_tts(tmp_path:Path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"stopped.sqlite3")
    store.set_setting("llm","local")
    store.set_setting("speak",False)
    ctrl=Controller(store)
    started=[]
    def streamed(*_):
        yield "Eine lange erste Aussage mit vielen Erklärungen. "
        started.append(True)
        time.sleep(.2)
        yield "Diese Zeile soll nicht erscheinen."
    monkeypatch.setattr(ctrl.gemma,"stream_answer",streamed)
    try:
        ctrl.send_text("Erkläre VLAN")
        assert wait_for(lambda:started)
        ctrl.stop()
        time.sleep(.25)
        QCoreApplication.processEvents()
        assert len(store.messages())==1
    finally:
        ctrl.close()
