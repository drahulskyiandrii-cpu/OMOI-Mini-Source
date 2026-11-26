"""Warmup-/Piper-Abbruch ohne echte Modelle oder Prozesse."""
import asyncio
import io
import json
import queue
import threading
import httpx
import pytest
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.local_tts import LocalTTSProcess
from omoi_mini.piper_tts import PiperTTSProcess
from omoi_mini.providers import ProviderFailure


def test_cancel_queued_warmup_does_not_start_any_engine(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("tts","local")
    ctrl=Controller(store)
    jobs,events=[],[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    monkeypatch.setattr(ctrl.local_voice,"prewarm",lambda *a:pytest.fail("stale warmup"))
    try:
        ctrl.prewarm_stack()
        ctrl.cancel_stack_warmup()
        jobs.pop()()
        assert not ctrl.stack_warming()
        assert events[-1]["state"]=="error"
        assert "abgebrochen" in str(events[-1])
    finally:ctrl.close()


def test_cancel_current_warmup_skips_next_engine(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("tts","local")
    store.set_setting("llm_mode","local_standard")
    ctrl=Controller(store)
    jobs=[]
    monkeypatch.setattr(ctrl.pool,"submit",jobs.append)
    def warm(active):
        assert active()
        ctrl.cancel_stack_warmup()
        assert not active()
    monkeypatch.setattr(ctrl.local_voice,"prewarm",warm)
    monkeypatch.setattr(ctrl.gemma,"ensure",lambda *a:pytest.fail("next engine"))
    try:
        ctrl.prewarm_stack()
        jobs.pop()()
        assert not ctrl.stack_warming()
    finally:ctrl.close()


def test_cancel_blocked_qwen_warmup(monkeypatch):
    engine=LocalTTSProcess()
    started,closed,stop=threading.Event(),threading.Event(),threading.Event()
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    async def handler(req):
        assert req.url.path=="/warmup"
        started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    errors=[]
    def run():
        try:engine.prewarm(lambda:not stop.is_set())
        except ProviderFailure as exc:errors.append(str(exc))
    t=threading.Thread(target=run,daemon=True)
    t.start()
    try:
        assert started.wait(2)
        stop.set()
        t.join(2)
        assert not t.is_alive() and closed.is_set()
        assert errors and "abgebrochen" in errors[0]
    finally:
        stop.set()
        t.join(2)
        engine.close()


def test_cancel_qwen_before_probe(monkeypatch):
    engine=LocalTTSProcess()
    monkeypatch.setattr(engine,"_ready",lambda *a:pytest.fail("probe"))
    with pytest.raises(ProviderFailure,match="abgebrochen"):engine.ensure(lambda:False)


@pytest.mark.parametrize("blocked",["write","reply"])
def test_cancel_piper_during_transport(monkeypatch,blocked):
    engine=PiperTTSProcess()
    started,stop,release=threading.Event(),threading.Event(),threading.Event()
    class Pipe:
        def write(self,text):
            started.set()
            if blocked=="write":release.wait(2)
        def flush(self):pass
    engine.proc=SimpleNamespace(stdin=Pipe())
    engine.lines=queue.Queue()
    monkeypatch.setattr(engine,"_ensure",lambda *a:None)
    closed=[]
    def close():
        closed.append(True)
        engine.proc=None
        release.set()
    monkeypatch.setattr(engine,"close",close)
    errors=[]
    def run():
        try:engine.synthesize("Hallo","de","de_DE-ramona-low",is_active=lambda:not stop.is_set())
        except ProviderFailure as exc:errors.append(str(exc))
    t=threading.Thread(target=run,daemon=True)
    t.start()
    try:
        assert started.wait(2)
        stop.set()
        t.join(2)
        assert not t.is_alive() and closed==[True]
        assert errors and "abgebrochen" in errors[0]
    finally:
        stop.set()
        release.set()
        t.join(2)


def test_cancel_piper_before_worker_start(monkeypatch):
    engine=PiperTTSProcess()
    monkeypatch.setattr("omoi_mini.piper_tts.spawn_external",lambda *a,**k:pytest.fail("spawn"))
    with pytest.raises(ProviderFailure,match="abgebrochen"):
        engine.synthesize("Hallo","de","de_DE-ramona-low",is_active=lambda:False)
