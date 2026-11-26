"""Keine GPU erforderlich: Modellwahl und Sprachtakt im Client."""
import io
import json
import wave

import httpx
import pytest

from omoi_mini.local_tts import local_tts_wav, local_tts_default_speed, local_tts_model_id
from omoi_mini.providers import ProviderFailure


def fake_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 500)
    return out.getvalue()


@pytest.mark.parametrize("size,expected,ru_speed", [
    ("0.6B", "qwen3-tts-0.6b-base", 1.15),
    ("1.7B", "qwen3-tts-1.7b-base", 1.0),
])
def test_model_size_and_ru_tempo(monkeypatch, size, expected, ru_speed):
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", size)
    assert local_tts_model_id() == expected
    assert local_tts_default_speed("ru") == ru_speed
    assert local_tts_default_speed("de") == 1.0


def test_invalid_model_selection(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "bogus")
    with pytest.raises(ProviderFailure):
        local_tts_model_id()


def test_russian_request_uses_owner_approved_speed(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "0.6B")
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT", "http://127.0.0.1:18190")
    captured = []
    real_client = httpx.AsyncClient
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=fake_wav(), request=request)
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    result = local_tts_wav("Привет!", "ru", "sad")
    assert result[:4] == b"RIFF"
    assert captured[0]["model"] == "qwen3-tts-0.6b-base"
    assert captured[0]["speed"] == 1.15
    assert captured[0]["voice"] == "reference"
    assert captured[0]["emotion"] == "sad"


def test_default_size_is_owner_selected_1_7b(monkeypatch):
    monkeypatch.delenv("OMOI_MINI_QWEN_SIZE", raising=False)
    assert local_tts_model_id() == "qwen3-tts-1.7b-base"
    assert local_tts_default_speed("ru") == 1.0


def test_different_model_on_same_port_is_not_reused(monkeypatch):
    from omoi_mini.local_tts import LocalTTSProcess

    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "1.7B")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"service": "omoi-mini-local-tts", "model": "qwen3-tts-0.6b-base"},
            request=request,
        )
    )
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    with pytest.raises(ProviderFailure, match="anderen Modell"):
        LocalTTSProcess()._ready("http://127.0.0.1:18190")
