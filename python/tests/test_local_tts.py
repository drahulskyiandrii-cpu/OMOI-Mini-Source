"""Lokaler TTS-HTTP-Vertrag: keine GPU, keine echten Netzwerkanfragen."""
import io
import wave
import httpx
import pytest
from omoi_mini.local_tts import local_tts_wav, _endpoint
from omoi_mini.providers import ProviderFailure

def fake_wav():
    out=io.BytesIO()
    with wave.open(out,"wb") as wav:
        wav.setframerate(24000)
        wav.setsampwidth(2)
        wav.setnchannels(1)
        wav.writeframes(b"\x00\x00"*200)
    return out.getvalue()

def test_tts_rejects_untrusted_endpoints(monkeypatch):
    for url in ("https://example.org:18190","http://192.168.0.1:18190",
                "http://127.0.0.1:18190/unsafe","http://user:password@127.0.0.1:18190"):
        monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT",url)
        with pytest.raises(ProviderFailure,match="Loopback"):
            _endpoint()

def test_tts_validates_text_language_emotion(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT","http://127.0.0.1:18190")
    with pytest.raises(ProviderFailure):
        local_tts_wav("","de","neutral")
    with pytest.raises(ProviderFailure):
        local_tts_wav("Hallo","fr","neutral")
    with pytest.raises(ProviderFailure):
        local_tts_wav("Hallo","de","angry")

def test_local_tts_request_includes_emotion_and_local_model(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT","http://127.0.0.1:18190")
    calls=[]
    transport=httpx.MockTransport(lambda req: (calls.append(req) or httpx.Response(200,content=fake_wav(),request=req)))
    real_client=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:real_client(transport=transport,**kwargs))
    result=local_tts_wav("Hallo!",language="de",emotion="happy")
    assert result.startswith(b"RIFF")
    assert calls[0].url.host=="127.0.0.1"
    assert b'"emotion":"happy"' in calls[0].content
    assert b'"model":"qwen3-tts-1.7b-base"' in calls[0].content
    assert b'"voice":"reference"' in calls[0].content
    assert b'"speed":1.0' in calls[0].content

def test_local_tts_rejects_invalid_audio(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT","http://127.0.0.1:18190")
    real_client=httpx.AsyncClient
    transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b"no audio",request=req))
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:real_client(transport=transport,**kwargs))
    with pytest.raises(ProviderFailure,match="WAV"):
        local_tts_wav("Hallo","de","neutral")


def test_automatic_endpoint_is_stable_and_avoids_occupied_port(monkeypatch):
    import socket
    from urllib.parse import urlparse
    from omoi_mini import local_tts
    monkeypatch.delenv("OMOI_MINI_TTS_ENDPOINT", raising=False)
    monkeypatch.setattr(local_tts,"_auto_endpoint",None)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1",0))
        endpoint=local_tts._endpoint()
        assert urlparse(endpoint).hostname=="127.0.0.1"
        assert urlparse(endpoint).port!=occupied.getsockname()[1]
        assert endpoint==local_tts._endpoint()
