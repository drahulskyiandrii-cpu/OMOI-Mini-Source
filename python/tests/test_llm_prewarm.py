"""ein gemeinsamer manueller Start, ohne Modelle oder GUI."""
import json
import threading
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.gui import Bridge
from omoi_mini.providers import LocalGemma, ProviderFailure


@pytest.mark.parametrize("mode", ["local_standard", "local_12b"])
def test_only_selected_llm_is_loaded_on_click(tmp_path, monkeypatch, mode):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("llm_mode",mode)
    ctrl=Controller(store)
    calls=[];jobs=[];events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:calls.append(ctrl.active_local_profile))
    for model in (ctrl.whisper,ctrl.cuda_whisper,ctrl.local_voice):
        monkeypatch.setattr(model,"ensure",lambda *a,**kw:pytest.fail("voice model started"))
    try:
        assert not ctrl.stack_warming()
        assert jobs==calls==[]
        bridge=SimpleNamespace(controller=ctrl,recording=False)
        Bridge.prewarmVoiceStack(bridge)
        Bridge.prewarmVoiceStack(bridge)
        assert len(jobs)==1 and ctrl.stack_warming()
        jobs.pop()()
        assert calls==["standard" if mode=="local_standard" else "gemma12"]
        assert events[-1]["state"]=="ready"
        assert events[-1]["completed"]==["LLM"]
    finally:
        ctrl.close()


def test_cloud_and_changed_profile_never_start_llm(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    ctrl=Controller(store)
    jobs=[];events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:pytest.fail("wrong model started"))
    try:
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="cloud"
        store.set_setting("llm_mode","local_12b")
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="error"
        store.set_setting("llm_mode","local_standard")
        ctrl.prewarm_stack()
        store.set_setting("llm_mode","cloud")
        jobs.pop()()
        assert events[-1]["state"]=="error"
        assert not ctrl.stack_warming()
    finally:
        ctrl.close()


def test_failure_allows_retry(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("llm_mode","local_standard")
    ctrl=Controller(store)
    jobs=[];events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    def fail(*a,**kw):raise ProviderFailure("test failure")
    monkeypatch.setattr(ctrl.gemma,"ensure",fail)
    try:
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="error"
        assert events[-1]["errors"]==[{"component":"LLM","message":"test failure"}]
        assert not ctrl.stack_warming()
        monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:None)
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="ready"
    finally:
        ctrl.close()


def test_selection_change_after_click_cannot_start_new_model(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    ctrl=Controller(store)
    jobs=[];events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:pytest.fail("new model started"))
    try:
        ctrl.prewarm_stack()
        ctrl.change_turn_setting("llm_mode","local_standard")
        jobs.pop()()
        assert events[-1]["state"]=="error"
        assert not ctrl.stack_warming()
    finally:
        ctrl.close()


def test_closed_engine_cannot_start(monkeypatch):
    engine=LocalGemma()
    engine.close()
    monkeypatch.setattr(engine,"_available",lambda:pytest.fail("health requested after close"))
    with pytest.raises(ProviderFailure,match="beendet"):
        engine.ensure()


def test_close_cancels_loading_owned_process(tmp_path, monkeypatch):
    model=tmp_path/"test.gguf"
    runtime=tmp_path/"llama-server.exe"
    model.write_bytes(b"GGUF")
    runtime.write_bytes(b"MZ")
    engine=LocalGemma(str(model),str(runtime),strict_owned=True)
    started=threading.Event()
    stopped=[]
    proc=SimpleNamespace(poll=lambda:None,terminate=lambda:stopped.append(True),wait=lambda **kw:0)
    def spawn(*args,**kwargs):
        started.set()
        return proc
    monkeypatch.setattr(engine,"_available",lambda:False)
    monkeypatch.setattr("omoi_mini.providers.subprocess.Popen",spawn)
    monkeypatch.setattr("omoi_mini.providers.httpx.get",lambda *a,**k:SimpleNamespace(status_code=503))
    errors=[]
    def load():
        try:engine.ensure()
        except ProviderFailure as exc:errors.append(str(exc))
    worker=threading.Thread(target=load)
    worker.start()
    assert started.wait(2)
    engine.close()
    worker.join(2)
    assert not worker.is_alive()
    assert stopped==[True] and engine.process is None
    assert errors

def test_missing_paths_report_separately(tmp_path,monkeypatch):
    model=tmp_path/"test.gguf"
    runtime=tmp_path/"llama-server.exe"
    engine=LocalGemma(str(model),str(runtime),strict_owned=True)
    monkeypatch.setattr(engine,"_available",lambda:False)
    with pytest.raises(ProviderFailure,match="llama-server.exe fehlt"):
        engine.ensure()
    runtime.write_bytes(b"MZ")
    with pytest.raises(ProviderFailure,match="GGUF-Modell fehlt"):
        engine.ensure()
