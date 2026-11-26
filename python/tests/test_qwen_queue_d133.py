"""Audio-FIFO ohne zahlreiche PCM-Puffer, ASGI-Lock ohne Modelle."""
import ast
import asyncio
import io
import threading
import wave
from pathlib import Path
from types import SimpleNamespace
import anyio
import httpx
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.local_tts import local_tts_wav
from omoi_mini.recorder import Recorder

ROOT=Path(__file__).resolve().parents[2]


def test_only_first_response_chunk_uses_live_pcm(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("tts","local")
    ctrl=Controller(store)
    calls=[]
    monkeypatch.setattr(ctrl,"_submit_voice",lambda pool,turn,fn,*args,**kw:calls.append((args,kw)))
    text="Ein erster Satz fuer die Wiedergabe. "+("Ein weiterer ausreichend langer Satz. "*80)
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",lambda *a:iter([text]))
    try:
        assert ctrl._stream_llm(0,[],"de","cloud")==text.strip()
        assert len(calls)>20
        assert [kw["stream_pcm"] for _,kw in calls]==[True]+[False]*(len(calls)-1)
    finally:ctrl.close()


def test_following_pcm_is_enqueued_as_complete_wav(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("tts","local")
    ctrl=Controller(store)
    events=[]
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a:None)
    pcm=bytes([1,0])*400
    def synth(*args,**kw):
        args[3](pcm[:400]);args[3](pcm[400:])
        return pcm
    monkeypatch.setattr("omoi_mini.controller.local_tts_pcm_stream",synth)
    ctrl.audio_stream_start.connect(lambda *a:events.append(("start",a)))
    ctrl.audio_stream_chunk.connect(lambda *a:events.append(("chunk",a)))
    ctrl.audio_stream_finish.connect(lambda *a:events.append(("finish",a)))
    ctrl.audio_enqueue.connect(lambda path,turn:events.append(("file",path)))
    try:
        for i in range(25):
            ctrl._speak("Synthetic sentence "+str(i),"de",0,enqueue=True,stream_pcm=i==0)
        assert [e[0] for e in events]==["start","chunk","chunk","finish"]+["file"]*24
        for _,path in events[4:]:
            with wave.open(path,"rb") as w:assert w.readframes(w.getnframes())==pcm
    finally:ctrl.close()


def test_local_wav_retries_busy_and_cancels(monkeypatch):
    real=httpx.AsyncClient
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(429) if len(calls)==1 else httpx.Response(200,content=Recorder._as_wav(bytes(800),24000))
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr("omoi_mini.local_tts.time.sleep",lambda _:None)
    assert local_tts_wav("Hallo") and len(calls)==2
    calls.clear()
    assert local_tts_wav("Hallo",is_active=lambda:not calls) is None
    assert len(calls)==1


def response_class(base,json_response,lock):
    source=ROOT/"local-tts/qwen_server.py"
    node=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
              if isinstance(n,ast.ClassDef) and n.name=="_LockedPCMResponse")
    scope=dict(StreamingResponse=base,JSONResponse=json_response,
               _lock=lock,threading=threading,anyio=anyio)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    return scope["_LockedPCMResponse"]


@pytest.mark.parametrize("disconnect",["before","during","normal"])
def test_lock_not_held_before_asgi_and_released_after_disconnect(disconnect):
    lock=threading.Lock()
    closed=[]
    class FakeStreaming:
        def __init__(self,content,**kw):self.content=content
        async def __call__(self,scope,receive,send):
            assert lock.locked()
            if disconnect=="before":raise OSError("disconnect")
            assert await anyio.to_thread.run_sync(next,self.content)==b"frame"
            if disconnect=="during":raise OSError("disconnect")
    class FakeJSON:
        def __init__(self,*a,**kw):pytest.fail("unexpected busy response")
    def chunks():
        try:yield b"frame"
        finally:closed.append(True)
    response=response_class(FakeStreaming,FakeJSON,lock)(chunks())
    assert not lock.locked()
    if disconnect=="normal":asyncio.run(response({},None,None))
    else:
        with pytest.raises(OSError):asyncio.run(response({},None,None))
    assert not lock.locked()
    assert closed==([] if disconnect=="before" else [True])


def test_busy_response_does_not_release_other_request_lock():
    lock=threading.Lock()
    statuses=[]
    class FakeStreaming:
        def __init__(self,content,**kw):pass
        async def __call__(self,*a):pytest.fail("concurrent synthesis")
    class FakeJSON:
        def __init__(self,body,status_code):statuses.append(status_code)
        async def __call__(self,*a):pass
    response=response_class(FakeStreaming,FakeJSON,lock)(iter(()))
    lock.acquire()
    try:
        asyncio.run(response({},None,None))
        assert statuses==[429] and lock.locked()
    finally:lock.release()
