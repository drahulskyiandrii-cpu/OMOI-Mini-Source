"""Turn-ID bis zur Qt-Oberflaeche; keine Modelle oder Audiogeraete."""
import json
from types import SimpleNamespace
from PySide6.QtCore import QObject, QCoreApplication, Qt, Slot
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge
from omoi_mini.providers import ProviderFailure


def test_queued_worker_ui_events_rejected_after_stop(tmp_path):
    app=QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    delivered=[]
    fake=SimpleNamespace(controller=ctrl,
        uiEvent=SimpleNamespace(emit=lambda raw:delivered.append(json.loads(raw))))
    class Receiver(QObject):
        @Slot(str)
        def deliver(self,raw):
            Bridge._forward_controller_event(fake,raw)
    receiver=Receiver()
    ctrl.event.connect(receiver.deliver,Qt.ConnectionType.QueuedConnection)
    try:
        old=ctrl.next_turn()
        for kind in ("stream_delta","status","stt_partial","stt_review","response","error"):
            ctrl.emit(kind,turn=old,value="alt")
        fresh=ctrl.stop()
        ctrl.emit("stream_delta",turn=fresh,text="neu")
        ctrl.emit("notice",value="Timer")
        app.processEvents()
        assert not any(e.get("turn")==old for e in delivered)
        assert any(e.get("text")=="neu" for e in delivered)
        assert any(e.get("value")=="Timer" for e in delivered)
        assert any(e["type"]=="animation_reset" and e["turn"]==fresh for e in delivered)
    finally:
        ctrl.close()


def test_stream_end_only_after_successful_history_write(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    ctrl.store.set_setting("llm","local")
    ctrl.store.set_setting("speak",False)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.gemma,"stream_answer",lambda *a:iter(["Hallo"]))
    def fail(*a):raise OSError("DB error")
    monkeypatch.setattr(ctrl.store,"add_message",fail)
    try:
        turn=ctrl.stop()
        ctrl._process(turn,"Testfrage")
        assert any(e["type"]=="stream_delta" and e["turn"]==turn for e in events)
        assert not any(e["type"]=="stream_end" for e in events)
        assert any(e["type"]=="stream_abort" and e["turn"]==turn+1 for e in events)
        assert not ctrl.current(turn)
        assert ctrl.store.messages()==[]
    finally:
        ctrl.close()


def test_tts_failure_does_not_abort_llm_text(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    ctrl.store.set_setting("tts","cartesia")
    ctrl.store.set_setting("speak",True)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    def fail(*a,**k):raise ProviderFailure("TTS")
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",fail)
    try:
        turn=ctrl.stop()
        ctrl._speak("Testausgabe","de",turn)
        assert any(e["type"]=="error" and e["turn"]==turn for e in events)
        assert not any(e["type"]=="stream_abort" for e in events)
    finally:
        ctrl.close()


def test_review_generation_reset_is_emitted(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:None)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    try:
        turn=ctrl.stop()
        ctrl._show_stt_review(turn,"cuda","de","Hallo")
        assert ctrl.submit_stt_review(turn,"Hallo",False)
        assert any(e["type"]=="animation_reset" and e["turn"]==turn+1 for e in events)
        turn=ctrl.stop()
        ctrl._show_stt_review(turn,"cuda","de","Hallo")
        assert ctrl.cancel_stt_review(turn)
        assert any(e["type"]=="animation_reset" and e["turn"]==turn+1 for e in events)
    finally:
        ctrl.close()
