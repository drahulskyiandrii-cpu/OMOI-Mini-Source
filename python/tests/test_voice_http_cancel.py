"""Abbruch und Groessenbegrenzung fuer Sprach-HTTP ohne externe API."""
import asyncio
import struct
import threading
import httpx
import pytest
from omoi_mini import providers,local_tts


class BlockedBody(httpx.AsyncByteStream):
    def __init__(self,started,closed,first):
        self.started=started
        self.closed=closed
        self.first=first

    async def __aiter__(self):
        if self.first:yield self.first
        self.started.set()
        await asyncio.Event().wait()

    async def aclose(self):self.closed.set()


@pytest.mark.parametrize("engine",["stt","cartesia","qwen_wav","qwen_pcm"])
@pytest.mark.parametrize("phase",["headers","body"])
def test_voice_http_cancel(monkeypatch,engine,phase):
    started,closed,stop=threading.Event(),threading.Event(),threading.Event()
    chunks=[]
    pcm=b"\x01\x00"*200
    monkeypatch.setenv("OPENAI_API_KEY","synthetic")
    monkeypatch.setenv("CARTESIA_API_KEY","synthetic")
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE","1.7B")
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT","http://127.0.0.1:18190")
    async def handler(request):
        if phase=="headers":
            started.set()
            try:await asyncio.Event().wait()
            finally:closed.set()
        headers={"X-OMOI-PCM-Rate":"24000","X-OMOI-PCM-Format":"s16le",
                 "X-OMOI-TTS-Model":"qwen3-tts-1.7b-base"}
        first=struct.pack(">I",len(pcm))+pcm if engine=="qwen_pcm" else b"partial"
        return httpx.Response(200,headers=headers,stream=BlockedBody(started,closed,first))
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    active=lambda:not stop.is_set()
    calls={"stt":lambda:providers.transcribe_openai(b"synthetic","de",is_active=active),
           "cartesia":lambda:providers.cartesia_wav("Test","de",voice_id="synthetic",is_active=active),
           "qwen_wav":lambda:local_tts.local_tts_wav("Test","de",is_active=active),
           "qwen_pcm":lambda:local_tts.local_tts_pcm_stream("Test","de","neutral",chunks.append,active)}
    result,errors=[],[]
    def run():
        try:result.append(calls[engine]())
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=run,daemon=True)
    thread.start()
    try:
        assert started.wait(3)
        stop.set()
        thread.join(2)
        assert not thread.is_alive() and closed.is_set()
        assert result==[None] and not errors
        assert chunks==([pcm] if engine=="qwen_pcm" and phase=="body" else [])
    finally:
        stop.set()
        thread.join(2)


def test_http_body_limit(monkeypatch):
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b"12345")),**kw))
    with pytest.raises(providers.ProviderFailure,match="gross"):
        providers.cancellable_post("http://127.0.0.1:1",is_active=lambda:True,timeout=2,max_bytes=4)


def test_malformed_cloud_stt_is_controlled_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic")
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"text":42})),**kw))
    with pytest.raises(providers.ProviderFailure,match="Keine Sprache"):
        providers.transcribe_openai(b"synthetic","de")

def test_compressed_response_not_decoded_twice(monkeypatch):
    import gzip
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(
        lambda r:httpx.Response(200,headers={"Content-Encoding":"gzip"},content=gzip.compress(b"ok"))),**kw))
    response=providers.cancellable_post("http://127.0.0.1:1",is_active=lambda:True,timeout=2)
    assert response.content==b"ok"
