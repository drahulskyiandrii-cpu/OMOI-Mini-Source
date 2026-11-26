"""Eigene Laufzeit nach Anbieterwechsel freigeben, ohne echte KI."""
import subprocess
import pytest
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini import external_process,local_stt,local_tts,piper_tts,providers


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c=Controller(Store(tmp_path/"db"))
    try:yield c
    finally:c.close()


@pytest.mark.parametrize("key,old,new,adapter,pool,attribute,tree",[
    ("llm_mode","local_standard","cloud","gemma","pool","process",False),
    ("stt","cuda","local","cuda_whisper","stt_pool","proc",True),
    ("stt","local","cloud","whisper","stt_pool","proc",False),
    ("tts","local","cartesia","local_voice","tts_pool","process",True),
    ("tts","piper","none","piper_voice","tts_pool","proc",True),
])
def test_deselected_model_released_without_loading_new_model(ctrl,monkeypatch,key,old,new,adapter,pool,attribute,tree):
    ctrl.store.set_setting(key,old)
    engine=getattr(ctrl,adapter)
    fake=object()
    setattr(engine,attribute,fake)
    tasks,stopped=[],[]
    monkeypatch.setattr(getattr(ctrl,pool),"submit",lambda fn,*args:tasks.append((fn,args)))
    module={"gemma":providers,"cuda_whisper":local_stt,"whisper":local_stt,
            "local_voice":local_tts,"piper_voice":piper_tts}[adapter]
    monkeypatch.setattr(module,"stop_owned_process",lambda proc,**kw:stopped.append((proc,kw.get("tree",False))) if proc is not None else None)
    turn=ctrl.next_turn()
    assert ctrl.change_turn_setting(key,new)
    assert not ctrl.current(turn)
    assert len(tasks)==1 and stopped==[]
    fn,args=tasks.pop();fn(*args)
    assert stopped==[(fake,tree)]
    assert getattr(engine,attribute) is None
    # The reusable adapter is not permanently shut down.
    event=getattr(engine,"_closed",None) or getattr(engine,"_shutdown",None)
    assert event is not None and not event.is_set()


def test_quick_reselection_skips_stale_unload(ctrl,monkeypatch):
    ctrl.store.set_setting("tts","local")
    tasks=[]
    monkeypatch.setattr(ctrl.tts_pool,"submit",lambda fn,*args:tasks.append((fn,args)))
    ctrl.change_turn_setting("tts","cartesia")
    ctrl.change_turn_setting("tts","local")
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda *a,**kw:pytest.fail("selected model unloaded"))
    fn,args=tasks.pop();fn(*args)
    assert not tasks
    # Avoid the patched helper during fixture shutdown.
    monkeypatch.undo()


def test_path_change_unloads_obsolete_llm(ctrl,monkeypatch):
    ctrl.store.set_setting("llm_mode","local_standard")
    tasks=[]
    monkeypatch.setattr(ctrl.pool,"submit",lambda fn,*args:tasks.append((fn,args)))
    ctrl.change_turn_setting("llm_model_file","new.gguf")
    assert len(tasks)==1
    assert tasks[0][1][0]=="llm"


def test_no_process_discovery_on_unload(monkeypatch):
    def forbidden(*a,**kw):pytest.fail("external process discovery")
    monkeypatch.setattr(subprocess,"run",forbidden)
    monkeypatch.setattr(subprocess,"Popen",forbidden)
    external_process.stop_owned_process(None,tree=True)
    providers.LocalGemma().unload_if(lambda:True)
    local_tts.LocalTTSProcess().unload_if(lambda:True)
    local_stt.LocalWhisper("cuda").unload_if(lambda:True)
    piper_tts.PiperTTSProcess().unload_if(lambda:True)


def test_process_wait_and_kill_fallback():
    calls=[]
    def wait(timeout):
        calls.append("wait")
        if calls.count("wait")==1:raise subprocess.TimeoutExpired("synthetic",timeout)
    proc=SimpleNamespace(poll=lambda:None,terminate=lambda:calls.append("terminate"),
        wait=wait,kill=lambda:calls.append("kill"))
    external_process.stop_owned_process(proc)
    assert calls==["terminate","wait","kill","wait"]


def test_failed_unload_keeps_owned_handle_for_retry(monkeypatch):
    engine=providers.LocalGemma()
    owned=object()
    engine.process=owned
    def fail(*a,**kw):raise OSError("synthetic")
    monkeypatch.setattr(providers,"stop_owned_process",fail)
    with pytest.raises(OSError):engine.unload_if(lambda:True)
    assert engine.process is owned
    monkeypatch.setattr(providers,"stop_owned_process",lambda *a,**kw:None)
    engine.unload_if(lambda:True)
    assert engine.process is None

@pytest.mark.parametrize("key,value",[("speak",False),("qwen_variant","cf050")])
def test_qwen_disabled_or_changed_profile_schedules_release(ctrl,monkeypatch,key,value):
    ctrl.store.set_setting("tts","local")
    tasks=[]
    monkeypatch.setattr(ctrl.tts_pool,"submit",lambda fn,*args:tasks.append((fn,args)))
    ctrl.change_turn_setting(key,value)
    assert len(tasks)==1 and tasks[0][1][0]=="qwen"
