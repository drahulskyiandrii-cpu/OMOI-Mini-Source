"""STT transport deadlines and priority without real processes/models."""
import ast
import io
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.providers import ProviderFailure
from omoi_mini.controller import Controller
from omoi_mini.core import Store

def test_old_reader_never_poison_new_queue():
    worker=LocalWhisper("cuda")
    old=queue.Queue()
    proc=SimpleNamespace(stdout=io.StringIO('{"id":1,"ok":true}\n'))
    worker._read_output(proc,old)
    assert old.get_nowait()["id"]==1
    assert old.get_nowait()["event"]=="fatal"
    assert worker.messages.empty()

@pytest.mark.parametrize("block_write",[True,False])
def test_pipe_and_response_have_one_deadline(monkeypatch,block_write):
    worker=LocalWhisper("cuda")
    gate=threading.Event()
    closed=[]
    class Stdin:
        def write(self,_):
            if block_write:gate.wait(2)
        def flush(self):pass
    proc=SimpleNamespace(stdin=Stdin())
    def close():
        closed.append(True)
        gate.set()
    monkeypatch.setattr(worker,"close",close)
    with pytest.raises(ProviderFailure,match="rechtzeitig"):
        worker._request(proc,queue.Queue(),{"id":1},.05)
    assert closed==[True]

def test_fatal_and_mismatched_id_discard_transport(monkeypatch):
    worker=LocalWhisper("cuda")
    closed=[]
    monkeypatch.setattr(worker,"close",lambda:closed.append(True))
    proc=SimpleNamespace(stdin=io.StringIO())
    for reply in ({"event":"fatal"},{"id":99,"ok":True}):
        q=queue.Queue();q.put(reply)
        with pytest.raises(ProviderFailure,match="unterbrochen"):
            worker._request(proc,q,{"id":1},.2)
    assert len(closed)==2

def test_cancel_final_wakes_transport(monkeypatch):
    worker=LocalWhisper("cuda")
    closed=[]
    monkeypatch.setattr(worker,"close",lambda:closed.append(True))
    with pytest.raises(ProviderFailure,match="abgebrochen"):
        worker._request(SimpleNamespace(stdin=io.StringIO()),queue.Queue(),{"id":1},10,lambda:False)
    assert closed==[True]

def test_partial_does_not_cold_start(monkeypatch):
    worker=LocalWhisper("cuda")
    monkeypatch.setattr(worker,"ensure",lambda *a,**kw:pytest.fail("cold partial"))
    with pytest.raises(ProviderFailure,match="nicht bereit"):
        worker.transcribe(b"fake","de",partial=True)

def test_partial_coalesces_and_final_has_dedicated_queue(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db");store.set_setting("stt","cuda")
    ctrl=Controller(store)
    jobs=[]
    monkeypatch.setattr(ctrl.cuda_whisper,"is_ready",lambda:True)
    monkeypatch.setattr(ctrl.stt_pool,"submit",lambda fn,*a:jobs.append((fn,a)))
    monkeypatch.setattr(ctrl.pool,"submit",lambda *a:pytest.fail("STT on LLM queue"))
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:pytest.fail("stale partial decoded"))
    try:
        turn=ctrl.stop()
        assert ctrl.partial_audio(b"a"*32044,turn)
        assert not ctrl.partial_audio(b"a"*64044,turn)
        ctrl.listen_audio(b"complete")
        assert len(jobs)==2
        fn,args=jobs.pop(0);fn(*args)
        assert not ctrl._partial_inflight
        assert not ctrl.current(turn)
        # Final queue item remains and is generation fenced.
        assert ctrl.voice_busy()
    finally:ctrl.close()

def test_low_confidence_requires_review(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db");store.set_setting("stt","cuda")
    ctrl=Controller(store)
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",lambda *a,**k:"Hallo")
    ctrl.cuda_whisper.last_uncertain=True
    reviews=[]
    monkeypatch.setattr(ctrl,"_show_stt_review",lambda *a:reviews.append(a))
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda *a:pytest.fail("uncertain result submitted"))
    try:
        ctrl._transcribe(ctrl.stop(),b"fake")
        assert reviews and reviews[0][-1]=="Hallo"
    finally:ctrl.close()

def test_segment_quality_joint_evidence():
    source=Path(__file__).resolve().parents[2]/"local-stt/cuda_worker.py"
    node=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(n,ast.FunctionDef) and n.name=="uncertain_segments")
    scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    f=scope["uncertain_segments"]
    assert not f([SimpleNamespace()])
    assert not f([SimpleNamespace(avg_logprob=-1.1,no_speech_prob=.1)])
    assert f([SimpleNamespace(avg_logprob=-1.1,no_speech_prob=.7)])
    assert f([SimpleNamespace(avg_logprob=-1.2,compression_ratio=2.5)])
    assert f([SimpleNamespace(compression_ratio=8)])

@pytest.mark.parametrize("encoding",["cp1252","cp850","utf-8"])
def test_cuda_worker_jsonl_is_ascii_safe(encoding):
    source=Path(__file__).resolve().parents[2]/"local-stt/cuda_worker.py"
    node=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(n,ast.FunctionDef) and n.name=="emit")
    binary=io.BytesIO()
    output=io.TextIOWrapper(binary,encoding=encoding)
    scope={"json":json,"sys":SimpleNamespace(stdout=output)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    payload={"id":1,"text":"Gr\u00fc\u00dfe, \u041f\u0440\u0438\u0432\u0435\u0442"}
    scope["emit"](payload)
    assert json.loads(binary.getvalue().decode("ascii"))==payload
