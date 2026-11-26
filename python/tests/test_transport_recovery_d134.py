"""Vollstaendige Streams, lokale Origins, genau ein Final je Aufnahme."""
import json
import threading
import httpx
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini import providers,local_tts
from omoi_mini.controller import Controller
from omoi_mini.core import Store


@pytest.mark.parametrize("local",[False,True])
@pytest.mark.parametrize("ending",["eof","done","explicit"])
def test_stream_completion_requires_terminal_event(monkeypatch,local,ending):
    event=({"choices":[{"delta":{"content":"Hallo"}}]} if local else
           {"type":"response.output_text.delta","delta":"Hallo"})
    raw="data: "+json.dumps(event)+"\n\n"
    if ending=="done":raw+="data: [DONE]\n\n"
    elif ending=="explicit":
        raw+=("data: [DONE]\n\n" if local else 'data: {"type":"response.completed"}\n\n')
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda req:httpx.Response(200,content=raw.encode())),**kw))
    monkeypatch.setenv("OPENAI_API_KEY","synthetic")
    engine=providers.LocalGemma()
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    fn=engine.stream_answer if local else providers.cloud_answer_stream
    iterator=fn([],"de")
    try:
        assert next(iterator)=="Hallo"
        if ending=="eof":
            with pytest.raises(providers.ProviderFailure,match="unvollstaendig"):next(iterator)
        else:
            with pytest.raises(StopIteration):next(iterator)
    finally:iterator.close();engine.close()


@pytest.mark.parametrize("endpoint",[
    "https://127.0.0.1:18189","http://example.org:18189",
    "http://127.0.0.1:18189/path","http://user:secret@localhost:18189",
    "http://localhost:18189?x=1","http://localhost:18189#fragment",
    "http://localhost","http://localhost:99999","http://[broken:18189",
])
def test_bad_local_endpoint_rejected_before_health(monkeypatch,endpoint):
    monkeypatch.setenv("OMOI_MINI_LLM_ENDPOINT",endpoint)
    engine=providers.LocalGemma()
    monkeypatch.setattr(engine,"_available",lambda:pytest.fail("invalid origin used"))
    with pytest.raises(providers.ProviderFailure,match="Loopback"):engine.ensure()
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT",endpoint)
    with pytest.raises(providers.ProviderFailure,match="Loopback"):local_tts._endpoint()
    engine.close()


def test_invalid_local_settings_do_not_break_cloud_controller(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE","invalid")
    monkeypatch.setenv("OMOI_MINI_LLM_ENDPOINT","http://invalid")
    ctrl=Controller(Store(tmp_path/"db"))
    try:
        assert ctrl.store.get_settings()["llm"]=="cloud"
        with pytest.raises(providers.ProviderFailure):ctrl.local_voice.ensure()
        with pytest.raises(providers.ProviderFailure):ctrl.gemma.ensure()
    finally:ctrl.close()


def test_duplicate_finish_signal_schedules_only_one_final(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    jobs=[]
    monkeypatch.setattr(ctrl.stt_pool,"submit",jobs.append)
    try:
        turn=ctrl.next_turn()
        ctrl.listen_audio(b"synthetic",turn)
        ctrl.listen_audio(b"synthetic",turn)
        assert len(jobs)==1
        fresh=ctrl.next_turn()
        ctrl.listen_audio(b"stale",turn)
        ctrl.listen_audio(b"new",fresh)
        assert len(jobs)==2
    finally:ctrl.close()
