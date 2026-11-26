"""SQLite-Verlauf und Qt-Turn-Reset bleiben konsistent."""
import json
import sqlite3
from types import SimpleNamespace
from PySide6.QtCore import QObject, QCoreApplication, Qt, Slot
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge


def test_committed_late_message_is_recovered_by_reset(tmp_path):
    app=QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    delivered=[]
    fake=SimpleNamespace(controller=ctrl,uiEvent=SimpleNamespace(
        emit=lambda raw:delivered.append(json.loads(raw))))
    class Receiver(QObject):
        @Slot(str)
        def deliver(self,raw):
            Bridge._forward_controller_event(fake,raw)
    receiver=Receiver()
    ctrl.event.connect(receiver.deliver,Qt.ConnectionType.QueuedConnection)
    try:
        turn=ctrl.next_turn()
        ctrl.store.add_message("assistant","Gespeichert")
        ctrl.emit("message",turn=turn,role="assistant",content="Gespeichert")
        fresh=ctrl.stop()
        app.processEvents()
        assert not any(e["type"]=="message" for e in delivered)
        reset=next(e for e in delivered if e["type"]=="animation_reset")
        assert reset["turn"]==fresh
        assert reset["history"]==[{"role":"assistant","content":"Gespeichert"}]
    finally:ctrl.close()


def test_clear_chat_invalidates_late_worker(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    try:
        turn=ctrl.next_turn()
        ctrl.store.add_message("user","Alt")
        def stream(*a):
            ctrl.clear_chat()
            return "Verspaetete Antwort"
        monkeypatch.setattr(ctrl,"_stream_llm",stream)
        ctrl._process(turn,"Testfrage")
        assert ctrl.store.messages()==[]
        assert events[-2]["type"]=="clear_chat"
        assert events[-2]["turn"]==turn+1
        reset=[e for e in events if e["type"]=="animation_reset"][-1]
        assert reset["history"]==[]
    finally:ctrl.close()


def test_database_read_failure_does_not_block_stop(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    stopped=[]
    ctrl.stop_audio.connect(stopped.append)
    try:
        turn=ctrl.next_turn()
        def fail(*a):raise sqlite3.OperationalError("busy")
        monkeypatch.setattr(ctrl.store,"messages",fail)
        assert ctrl.stop()==turn+1
        assert stopped[-1]==turn+1
        assert not ctrl.current(turn)
    finally:ctrl.close()
