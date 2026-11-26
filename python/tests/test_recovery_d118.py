"""Begrenztes JSONL und echte synthetische Prozess-Recovery."""
import io
import json
import queue
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.external_process import read_worker_events
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.providers import ProviderFailure


@pytest.mark.parametrize("data,limit",[("x"*1000,100),('{"id":1}\\n'*20,100)])
def test_reader_overflow_signals_failure_without_blocking(data,limit):
    messages=queue.Queue(maxsize=2)
    thread=threading.Thread(target=read_worker_events,
        args=(SimpleNamespace(stdout=io.StringIO(data.replace("\\n","\n"))),messages,limit),
        daemon=True)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    items=[]
    while not messages.empty():items.append(messages.get_nowait())
    assert items[-1]["event"]=="fatal"


def test_crashed_pipe_can_be_replaced_and_next_request_succeeds():
    # A synthetic worker exits on the first request; replacement echoes JSON.
    worker=LocalWhisper("openvino")
    def attach(code):
        proc=subprocess.Popen([sys.executable,"-u","-c",code],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            encoding="utf-8")
        worker.proc=proc
        worker.messages=queue.Queue(maxsize=16)
        reader=threading.Thread(target=worker._read_output,args=(proc,worker.messages),daemon=True)
        reader.start()
        return proc,reader
    proc,reader=attach("import sys; sys.stdin.readline()")
    try:
        with pytest.raises(ProviderFailure,match="unterbrochen"):
            worker._request(proc,worker.messages,{"id":1},3)
        assert worker.proc is None and proc.poll() is not None
        reader.join(2)
        proc2,reader2=attach("import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps(dict(id=r['id'],ok=True,text='Hallo')),flush=True)")
        try:
            assert worker._request(proc2,worker.messages,{"id":2},3)["text"]=="Hallo"
        finally:
            worker.shutdown()
            reader2.join(2)
            proc2.stdin.close();proc2.stdout.close()
    finally:
        worker.shutdown()
        reader.join(2)
        proc.stdin.close();proc.stdout.close()


@pytest.mark.parametrize("bad",[42,{"bad":"text"},"x"*4001])
def test_invalid_stt_reply_is_rejected(monkeypatch,bad):
    worker=LocalWhisper("openvino")
    worker.proc=SimpleNamespace(stdin=True)
    monkeypatch.setattr(worker,"ensure",lambda *a,**kw:None)
    monkeypatch.setattr(worker,"_request",lambda *a:{"ok":True,"text":bad})
    stopped=[]
    monkeypatch.setattr(worker,"close",lambda:stopped.append(True))
    with pytest.raises(ProviderFailure,match="STT-Antwort"):worker.transcribe(b"test","de")
    assert stopped==[True]


def test_failed_user_db_write_does_not_call_llm(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def fail(*a):raise sqlite3.OperationalError("busy")
    monkeypatch.setattr(ctrl.store,"add_message",fail)
    monkeypatch.setattr(ctrl.pool,"submit",lambda *a:pytest.fail("LLM started"))
    try:
        ctrl.send_text("Hallo")
        assert events[-1]["avatarState"]=="idle"
        assert any(e["type"]=="error" for e in events)
        assert not ctrl.voice_busy()
        assert ctrl.store.messages()==[]
    finally:ctrl.close()
