"""lifecycle regression gates. Synthetic workers, no models or GPU."""
import io
import json
import queue
import subprocess
import sys
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.local_tts import LocalTTSProcess
from omoi_mini.piper_tts import PiperTTSProcess
from omoi_mini.providers import ProviderFailure


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    controller=Controller(Store(tmp_path/"db"))
    try:yield controller
    finally:controller.close()


def test_stack_settings_failure_releases_controls(ctrl,monkeypatch):
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    jobs=[]
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    ctrl.prewarm_stack()
    with monkeypatch.context() as m:
        def broken():raise OSError("private path")
        m.setattr(ctrl.store,"get_settings",broken)
        jobs.pop()()
    assert not ctrl.stack_warming()
    assert events[-1]["state"]=="error"
    assert "private path" not in str(events)
    ctrl.prewarm_stack()
    jobs.pop()()
    assert events[-1]["state"]=="cloud"


def test_stack_change_mid_start_does_not_start_old_selection(ctrl,monkeypatch):
    ctrl.store.set_setting("stt","cuda")
    ctrl.store.set_setting("tts","local")
    calls=[];events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def change(*a,**kw):
        calls.append("stt")
        ctrl.store.set_setting("tts","cartesia")
    monkeypatch.setattr(ctrl.cuda_whisper,"ensure",change)
    monkeypatch.setattr(ctrl.local_voice,"prewarm",lambda *a,**kw:pytest.fail("old TTS started"))
    ctrl._prewarm_stack_worker()
    assert calls==["stt"]
    assert events[-1]["state"]=="error"
    assert not ctrl.stack_warming()


def test_stale_final_cannot_create_new_turn_or_write_chat(ctrl,monkeypatch):
    ctrl.store.set_setting("stt","cuda")
    turn=ctrl.stop()
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:"Hallo")
    original=ctrl._dispatch_text
    def stop_at_dispatch(*args):
        ctrl.stop()
        original(*args)
    monkeypatch.setattr(ctrl,"_dispatch_text",stop_at_dispatch)
    monkeypatch.setattr(ctrl.pool,"submit",lambda *a:pytest.fail("stale LLM"))
    ctrl._transcribe(turn,b"test")
    assert ctrl.store.messages()==[]


def test_recognition_same_turn_on_success(ctrl,monkeypatch):
    ctrl.store.set_setting("stt","cuda")
    turn=ctrl.stop()
    calls=[]
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:"Hallo")
    monkeypatch.setattr(ctrl.pool,"submit",lambda fn:calls.append(fn))
    ctrl._transcribe(turn,b"test")
    assert ctrl.current(turn)
    assert len(calls)==1
    assert ctrl.store.messages()==[{"role":"user","content":"Hallo"}]


def test_stt_settings_failure_reports_idle(ctrl,monkeypatch):
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    turn=ctrl.stop()
    def fail():raise ValueError("private setting")
    with monkeypatch.context() as m:
        m.setattr(ctrl.store,"get_settings",fail)
        ctrl._transcribe(turn,b"test")
    assert events[-1]["avatarState"]=="idle"
    assert any(e["type"]=="error" for e in events)


def test_changed_qwen_profile_not_used_even_with_cache(ctrl,monkeypatch):
    ctrl.store.set_setting("tts","local")
    ctrl.store.set_setting("qwen_variant","cf050")
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:pytest.fail("old voice"))
    monkeypatch.setattr(ctrl,"_cache_path",lambda *a,**k:pytest.fail("old cache"))
    events=[];ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    ctrl._speak("Hallo","de",ctrl.stop())
    assert any(e["type"]=="error" for e in events)


@pytest.mark.parametrize("engine",["cuda","openvino"])
def test_stt_cannot_spawn_after_shutdown(engine,monkeypatch):
    stt=LocalWhisper(engine)
    stt.shutdown()
    monkeypatch.setattr("omoi_mini.local_stt.spawn_external",lambda *a,**k:pytest.fail("spawn"))
    with pytest.raises(ProviderFailure,match="abgebrochen"):
        stt.ensure()


def test_qwen_cannot_probe_after_shutdown(monkeypatch):
    tts=LocalTTSProcess()
    tts.close()
    monkeypatch.setattr(tts,"_ready",lambda *a:pytest.fail("probe"))
    with pytest.raises(ProviderFailure,match="beendet"):tts.ensure()


def test_piper_reader_cannot_poison_replacement_queue():
    tts=PiperTTSProcess()
    old=queue.Queue()
    tts._read_lines(SimpleNamespace(stdout=io.StringIO('{"id":1}\n')),old)
    assert old.get_nowait()["id"]==1
    assert old.get_nowait()["event"]=="fatal"
    assert tts.lines.empty()
    tts.shutdown()
    with pytest.raises(ProviderFailure,match="beendet"):tts._ensure()


def test_real_synthetic_pipe_preserves_unicode_and_request_order():
    # Child only echoes a synthetic JSON request. No models, no audio.
    code="import sys,json;\\nfor line in sys.stdin:\\n r=json.loads(line); print(json.dumps(dict(id=r['id'],ok=True,text=r['text'])),flush=True)"
    code=code.replace("\\n","\n")
    proc=subprocess.Popen([sys.executable,"-u","-c",code],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,encoding="utf-8")
    worker=LocalWhisper("openvino")
    worker.proc=proc
    import threading
    reader=threading.Thread(target=worker._read_output,args=(proc,worker.messages),daemon=True)
    reader.start()
    try:
        for i,text in enumerate(["Gr\u00fc\u00dfe","\u041f\u0440\u0438\u0432\u0435\u0442"],1):
            reply=worker._request(proc,worker.messages,{"id":i,"text":text},5)
            assert reply=={"id":i,"ok":True,"text":text}
    finally:
        worker.shutdown()
        reader.join(2)


def test_assistant_history_write_is_atomic_with_stop(ctrl,monkeypatch):
    ctrl.store.set_setting("speak",False)
    original=ctrl.store.add_message
    def checked_write(role,text,provider):
        assert ctrl._lock.locked()
        original(role,text,provider)
    monkeypatch.setattr(ctrl.store,"add_message",checked_write)
    monkeypatch.setattr(ctrl,"_stream_llm",lambda *a:"Antwort")
    ctrl._process(ctrl.stop(),"Testfrage")
    assert ctrl.store.messages()==[{"role":"assistant","content":"Antwort"}]


def test_cancelled_llm_answer_is_not_saved(ctrl,monkeypatch):
    ctrl.store.set_setting("speak",False)
    def cancelled(*args):
        ctrl.stop()
        return "Veraltete Antwort"
    monkeypatch.setattr(ctrl,"_stream_llm",cancelled)
    ctrl._process(ctrl.stop(),"Testfrage")
    assert ctrl.store.messages()==[]
