"""one manual stack start; all backends mocked."""
import json
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.gui import Bridge
from omoi_mini.providers import ProviderFailure


@pytest.mark.parametrize("stt",["local","cuda"])
def test_manual_stack_uses_only_selected_backends(tmp_path,monkeypatch,stt):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("llm_mode","local_standard")
    store.set_setting("stt",stt)
    store.set_setting("tts","local")
    ctrl=Controller(store)
    jobs=[]; calls=[]; events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.whisper,"ensure",lambda *a,**kw:calls.append("local"))
    monkeypatch.setattr(ctrl.cuda_whisper,"ensure",lambda *a,**kw:calls.append("cuda"))
    monkeypatch.setattr(ctrl.local_voice,"prewarm",lambda *a,**kw:calls.append("TTS"))
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:calls.append("LLM"))
    try:
        bridge=SimpleNamespace(controller=ctrl,recording=False)
        Bridge.prewarmVoiceStack(bridge)
        Bridge.prewarmVoiceStack(bridge)
        assert len(jobs)==1 and ctrl.stack_warming()
        jobs.pop()()
        assert calls==[stt,"TTS","LLM"]
        assert not ctrl.stack_warming()
        assert events[-1]["state"]=="ready"
        assert events[-1]["completed"]==["STT","TTS","LLM"]
    finally:ctrl.close()


def test_failure_is_visible_other_components_still_start(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("llm_mode","local_standard")
    store.set_setting("stt","local")
    store.set_setting("tts","local")
    ctrl=Controller(store)
    jobs=[]; calls=[]; events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.whisper,"ensure",lambda *a,**kw:calls.append("STT"))
    def fail(*a,**kw):raise ProviderFailure("Qwen: CUDA-GPU nicht verfuegbar.")
    monkeypatch.setattr(ctrl.local_voice,"prewarm",fail)
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a,**kw:calls.append("LLM"))
    try:
        ctrl.prewarm_stack();jobs.pop()()
        assert calls==["STT","LLM"]
        assert events[-1]["state"]=="error"
        assert events[-1]["errors"][0]["component"]=="TTS"
        assert not ctrl.stack_warming()
        monkeypatch.setattr(ctrl.local_voice,"prewarm",lambda *a,**kw:calls.append("TTS"))
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="ready"
    finally:ctrl.close()


def test_cloud_has_no_network_or_local_calls(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    ctrl=Controller(store)
    jobs=[]; events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    for engine in (ctrl.gemma,ctrl.whisper,ctrl.cuda_whisper):
        monkeypatch.setattr(engine,"ensure",lambda *a,**kw:pytest.fail("unexpected model"))
    monkeypatch.setattr(ctrl.local_voice,"prewarm",lambda *a,**kw:pytest.fail("unexpected TTS"))
    try:
        ctrl.prewarm_stack();jobs.pop()()
        assert events[-1]["state"]=="cloud"
    finally:ctrl.close()


def test_qwen_warmup_reports_safe_error_code(monkeypatch):
    import httpx
    from omoi_mini.local_tts import LocalTTSProcess
    engine=LocalTTSProcess()
    monkeypatch.setattr(engine,"ensure",lambda *a,**kw:None)
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(
        lambda req:httpx.Response(503,json={"detail":"CUDA_OUT_OF_MEMORY"})),**kw))
    with pytest.raises(ProviderFailure,match="GPU-Speicher"):
        engine.prewarm()

def test_frozen_worker_dll_directory_is_restored_on_failure(monkeypatch):
    import ctypes
    from omoi_mini import external_process as ext
    calls=[]
    def get_directory(size,buffer):
        buffer.value="mini-bundle"
        return len(buffer.value)
    def set_directory(value):
        calls.append(value)
        return 1
    monkeypatch.setattr(ext,"os",SimpleNamespace(name="nt"))
    monkeypatch.setattr(ext,"sys",SimpleNamespace(frozen=True,_MEIPASS="mini-bundle"))
    monkeypatch.setattr(ctypes,"windll",SimpleNamespace(kernel32=SimpleNamespace(
        GetDllDirectoryW=get_directory,SetDllDirectoryW=set_directory)),raising=False)
    def fail(*args,**kwargs):
        assert calls==[None]
        raise OSError("synthetic")
    monkeypatch.setattr(ext.subprocess,"Popen",fail)
    with pytest.raises(OSError,match="synthetic"):
        ext.spawn_external(["fake.exe"])
    assert calls==[None,"mini-bundle"]
