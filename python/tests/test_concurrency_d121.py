"""Warteschlangen, parallele Anbieterwechsel und begrenzte Antworten."""
import gc
import json
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini import providers


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c=Controller(Store(tmp_path/"db"))
    try:yield c
    finally:c.close()


def test_cancel_releases_queued_audio_payload_before_executor_runs(ctrl):
    class Payload:pass
    payload=Payload()
    weak=weakref.ref(payload)
    tasks=[]
    turn=ctrl.next_turn()
    ctrl._submit_voice(SimpleNamespace(submit=tasks.append),turn,
                       lambda data:pytest.fail("stale job"),payload)
    del payload
    assert weak() is not None
    ctrl.stop()
    gc.collect()
    assert weak() is None
    assert len(tasks)==1
    tasks.pop()()
    assert not ctrl.voice_busy()


def test_real_executor_stop_storm_does_not_hold_old_payloads(ctrl):
    started,release=threading.Event(),threading.Event()
    old=[]
    executed=[]
    class Payload:pass
    try:
        turn=ctrl.next_turn()
        def blocked():
            started.set()
            release.wait(5)
        active=ctrl._submit_voice(ctrl.stt_pool,turn,blocked)
        assert started.wait(2)
        for i in range(120):
            payload=Payload()
            old.append(weakref.ref(payload))
            ctrl._submit_voice(ctrl.stt_pool,turn,lambda x:executed.append(x),payload)
            del payload
            turn=ctrl.stop()
        gc.collect()
        assert all(ref() is None for ref in old)
        final=ctrl._submit_voice(ctrl.stt_pool,turn,lambda:executed.append("final"))
        release.set()
        active.result(3)
        final.result(3)
        assert executed==["final"]
        assert not ctrl.voice_busy()
        assert not ctrl._pending_voice
    finally:release.set()


def test_parallel_switches_with_real_pools_do_not_deadlock(ctrl,monkeypatch):
    # No owned process exists; never probe or spawn any model.
    calls=[]
    monkeypatch.setattr(providers,"stop_owned_process",lambda proc,**kw:calls.append(proc))
    gate=threading.Barrier(3)
    def switch(key,values):
        gate.wait(3)
        for i in range(30):ctrl.change_turn_setting(key,values[i%2])
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(switch,"llm_mode",("local_standard","cloud")),
                 pool.submit(switch,"stt",("cuda","cloud")),
                 pool.submit(switch,"tts",("local","cartesia"))]
        for future in futures:future.result(10)
    ctrl.pool.submit(lambda:None).result(5)
    ctrl.stt_pool.submit(lambda:None).result(5)
    ctrl.tts_pool.submit(lambda:None).result(5)
    settings=ctrl.store.get_settings()
    assert (settings["llm"],settings["stt"],settings["tts"])==("cloud","cloud","cartesia")
    assert not ctrl.voice_busy()
    assert all(proc is None for proc in calls)


def test_oversize_llm_output_is_consistent_with_history(ctrl,monkeypatch):
    ctrl.store.set_setting("speak",False)
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    closed=[]
    def stream(*a):
        try:
            yield "a"*9000
            yield "b"*2000
            pytest.fail("response limit exceeded")
        finally:closed.append(True)
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",stream)
    ctrl._process(ctrl.next_turn(),"test")
    answer="a"*9000+"b"*1000
    assert ctrl.store.messages()[-1]["content"]==answer
    assert "".join(e["text"] for e in events if e["type"]=="stream_delta")==answer
    assert next(e["text"] for e in events if e["type"]=="stream_end")==answer
    assert closed==[True]


class Chunked(httpx.AsyncByteStream):
    def __init__(self,parts):self.parts=parts
    async def __aiter__(self):
        for p in self.parts:yield p


@pytest.mark.parametrize("parts,match",[
    ([b"data: "+b"x"*270000],"zu gross"),
    ([b'data: {"type":"response.output_text.delta"'],"unvollstaendig"),
])
def test_sse_rejects_oversize_and_truncated_events(monkeypatch,parts,match):
    real=httpx.AsyncClient
    monkeypatch.setenv("OPENAI_API_KEY","synthetic")
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Chunked(parts))),**kw))
    with pytest.raises(providers.ProviderFailure,match=match):
        list(providers.cloud_answer_stream([],"de"))


def test_sse_unicode_across_arbitrary_network_chunks(monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setenv("OPENAI_API_KEY","synthetic")
    expected="Gr\u00fc\u00dfe \u043c\u0438\u0440"
    raw=("data: "+json.dumps({"type":"response.output_text.delta","delta":expected},ensure_ascii=False)+"\r\n\r\ndata: [DONE]\r\n").encode()
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Chunked([bytes([b]) for b in raw]))),**kw))
    assert list(providers.cloud_answer_stream([],"de"))==[expected]
