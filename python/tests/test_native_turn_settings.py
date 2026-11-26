"""Native Pfadwahl darf keine aktive Aufnahme umleiten."""
import json
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication
from omoi_mini import gui
from omoi_mini.core import Store
from omoi_mini.controller import Controller


def test_native_model_change_cancels_old_turn(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    bridge=SimpleNamespace(window=SimpleNamespace(store=ctrl.store),recording=False,
        controller=ctrl,uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    target=tmp_path/"model.gguf"
    target.write_bytes(b"GGUF")
    monkeypatch.setattr(gui.QFileDialog,"getOpenFileName",lambda *a:(str(target),""))
    try:
        turn=ctrl.next_turn()
        gui.Bridge.chooseLocalGGUF(bridge)
        assert not ctrl.current(turn)
        assert ctrl.store.get_settings()["llm_model_file"]==str(target.resolve())
        assert events[-1]["type"]=="notice"
    finally:ctrl.close()


def test_recording_started_inside_file_dialog_rejects_path(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    bridge=SimpleNamespace(window=SimpleNamespace(store=ctrl.store),recording=False,
        controller=ctrl,uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    target=tmp_path/"model.gguf"
    target.write_bytes(b"GGUF")
    def choose(*a):
        bridge.recording=True
        return str(target),""
    monkeypatch.setattr(gui.QFileDialog,"getOpenFileName",choose)
    try:
        turn=ctrl.next_turn()
        gui.Bridge.chooseLocalGGUF(bridge)
        assert ctrl.current(turn)
        assert ctrl.store.get_settings()["llm_model_file"]==""
        assert events[-1]["type"]=="error"
        gui.Bridge.saveSetting(bridge,"qwen_voice_reference",json.dumps("untrusted.wav"))
        assert ctrl.store.get_settings()["qwen_voice_reference"]==""
    finally:ctrl.close()
