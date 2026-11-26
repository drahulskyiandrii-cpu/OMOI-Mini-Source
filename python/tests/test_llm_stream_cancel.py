"""Abbruch einer blockierten HTTP-Verbindung ohne echte API."""
import asyncio
import threading
import time
import httpx
import pytest
from omoi_mini import providers


class BlockedStream(httpx.AsyncByteStream):
    def __init__(self,started,closed,first):
        self.started=started
        self.closed=closed
        self.first=first
    async def __aiter__(self):
        if self.first:yield self.first
        self.started.set()
        await asyncio.Event().wait()
    async def aclose(self):
        self.closed.set()


@pytest.mark.parametrize("local",[False,True])
@pytest.mark.parametrize("first",[False,True])
def test_stop_closes_blocked_http_stream(monkeypatch,local,first):
    started,closed,stop=threading.Event(),threading.Event(),threading.Event()
    line=(b'data: {"choices":[{"delta":{"content":"Hallo"}}]}\n\n' if local else
          b'data: {"type":"response.output_text.delta","delta":"Hallo"}\n\n')
    async def handler(request):
        return httpx.Response(200,stream=BlockedStream(started,closed,line if first else b""))
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv("OPENAI_API_KEY","fake-test-key")
    engine=providers.LocalGemma()
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    fn=engine.stream_answer if local else providers.cloud_answer_stream
    output,errors=[],[]
    def work():
        try:output.extend(fn([{"role":"user","content":"Test"}],"de",lambda:not stop.is_set()))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=work,daemon=True)
    thread.start()
    try:
        assert started.wait(3)
        begin=time.monotonic()
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        assert time.monotonic()-begin<2
        assert closed.is_set()
        assert errors==[]
        assert output==(["Hallo"] if first else [])
    finally:
        stop.set()
        thread.join(2)
        engine.close()


def test_cancelled_stream_does_not_open_http(monkeypatch):
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:pytest.fail("unexpected HTTP"))
    assert list(providers.cloud_answer_stream([],"de",lambda:False))==[]
    engine=providers.LocalGemma()
    monkeypatch.setattr(engine,"ensure",lambda *a:pytest.fail("unexpected model start"))
    assert list(engine.stream_answer([],"de",lambda:False))==[]


def test_normal_cloud_stream_stops_at_done(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","fake-test-key")
    async def handler(request):
        return httpx.Response(200,content=b'data: {"type":"response.output_text.delta","delta":"Hallo"}\n\ndata: [DONE]\n\ndata: {"type":"response.output_text.delta","delta":"alt"}\n\n')
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    assert list(providers.cloud_answer_stream([],"de"))==["Hallo"]


def test_cancel_waiting_for_local_start_lock():
    engine=providers.LocalGemma()
    stop=threading.Event()
    errors=[]
    def work():
        try:engine.ensure(lambda:not stop.is_set())
        except providers.ProviderFailure as exc:errors.append(str(exc))
    with engine.lock:
        worker=threading.Thread(target=work,daemon=True)
        worker.start()
        stop.set()
        worker.join(2)
        assert not worker.is_alive()
    assert errors and engine.process is None
    engine.close()

def test_cancel_real_loopback_sse_after_first_token(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    release=threading.Event()
    got_token=threading.Event()
    stop=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length",0)))
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"Hallo"}}]}\n\n')
            self.wfile.flush()
            release.wait(5)
    with ThreadingHTTPServer(("127.0.0.1",0),Handler) as server:
        server_thread=threading.Thread(target=server.serve_forever,daemon=True)
        server_thread.start()
        monkeypatch.setenv("OMOI_MINI_LLM_ENDPOINT",f"http://127.0.0.1:{server.server_port}")
        engine=providers.LocalGemma()
        monkeypatch.setattr(engine,"ensure",lambda *a:None)
        output,errors=[],[]
        def work():
            try:
                for token in engine.stream_answer([],"de",lambda:not stop.is_set()):
                    output.append(token)
                    got_token.set()
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=work,daemon=True)
        thread.start()
        try:
            assert got_token.wait(3)
            stop.set()
            thread.join(2)
            assert not thread.is_alive()
            assert output==["Hallo"] and errors==[]
        finally:
            stop.set()
            release.set()
            thread.join(2)
            server.shutdown()
            server_thread.join(2)
            engine.close()
