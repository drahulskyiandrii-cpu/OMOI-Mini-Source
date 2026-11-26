"""Offline PCM-Streamvertrag und frühe Wiedergabe ohne GPU/Cloud."""
from __future__ import annotations

import io
import json
import struct
import wave
from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import QCoreApplication

from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.local_tts import local_tts_pcm_stream
from omoi_mini.providers import ProviderFailure
from omoi_mini.streaming import SpeechChunker


HEADERS = {
    "X-OMOI-PCM-Rate": "24000",
    "X-OMOI-PCM-Format": "s16le",
    "X-OMOI-TTS-Model": "qwen3-tts-1.7b-base",
}


def stream_frame(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def test_pcm_frame_stream_starts_before_end(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT", "http://127.0.0.1:18190")
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "1.7B")
    audio1 = b"\x01\x00" * 4096
    audio2 = b"\x02\x00" * 4096
    order = []

    def handler(req):
        assert str(req.url).endswith("/v1/audio/stream")
        data = json.loads(req.content)
        assert data["response_format"] == "pcm"
        order.append("request")
        return httpx.Response(
            200, headers=HEADERS,
            content=stream_frame(audio1)+stream_frame(audio2)+struct.pack(">I", 0),
            request=req,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    output = local_tts_pcm_stream(
        "Ein VLAN trennt Netzwerke.", "de", "neutral",
        lambda pcm: order.append("chunk") or None,
    )
    assert output == audio1+audio2
    assert order == ["request", "chunk", "chunk"]


@pytest.mark.parametrize("bad", [
    struct.pack(">I", 0xFFFFFFFF),
    stream_frame(b"\x00\x00"*1024),
    struct.pack(">I", 3)+b"123",
    struct.pack(">I", 2097152),
])
def test_pcm_protocol_rejects_incomplete_or_error(monkeypatch, bad):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT", "http://127.0.0.1:18190")
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "1.7B")
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, headers=HEADERS, content=bad, request=req)
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    with pytest.raises(ProviderFailure):
        local_tts_pcm_stream("Ein VLAN.", "de", "neutral", lambda pcm: None)


def test_pcm_abort_before_network_request(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Network should not be called")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    assert local_tts_pcm_stream(
        "Eine Phrase.", "de", "neutral", lambda pcm: None,
        is_active=lambda: False,
    ) is None


def test_early_first_german_sentence():
    chunker = SpeechChunker()
    phrase = "Ein VLAN trennt Netzwerke. "
    assert chunker.feed(phrase) == [phrase.strip()]
    assert chunker.feed("Das spart Ressourcen.") == []
    assert chunker.finish() == ["Das spart Ressourcen."]


def test_local_stream_controller_forwards_pcm_and_caches_after_done(
    monkeypatch, tmp_path: Path,
):
    QCoreApplication.instance() or QCoreApplication([])
    db = Store(tmp_path/"stream.sqlite3")
    db.set_setting("tts", "local")
    db.set_setting("speak", True)
    ctrl = Controller(db)
    turn = ctrl.next_turn()
    pcm = b"\x03\x00" * 4800
    collected = []
    ctrl.audio_stream_start.connect(
        lambda token, rate, request_turn: collected.append(("start", rate, request_turn))
    )
    ctrl.audio_stream_chunk.connect(lambda token, data: collected.append(("chunk", len(data))))
    ctrl.audio_stream_finish.connect(lambda token: collected.append(("done",)))
    monkeypatch.setattr(ctrl.local_voice, "ensure", lambda *a,**kw: None)
    def streaming(text, lang, emotion, callback, is_active):
        callback(pcm[:4000])
        callback(pcm[4000:])
        return pcm
    monkeypatch.setattr("omoi_mini.controller.local_tts_pcm_stream", streaming)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir", lambda: tmp_path)
    try:
        ctrl._speak("Neue Antwort einmalig", "de", turn, enqueue=True)
        assert collected == [
            ("start", 24000, turn), ("chunk", 4000),
            ("chunk", len(pcm)-4000), ("done",),
        ]
        saved = ctrl._cache_path("Neue Antwort einmalig", "de", "local")
        with wave.open(str(saved), "rb") as file:
            assert file.getframerate() == 24000
            assert file.getnframes() == len(pcm)//2
    finally:
        ctrl.close()


def test_pcm_busy_retries_after_warmup(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT", "http://127.0.0.1:18190")
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "1.7B")
    monkeypatch.setattr("omoi_mini.local_tts.time.sleep", lambda _: None)
    seen = []
    pcm = b"\x00\x00" * 700
    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, request=req)
        return httpx.Response(
            200, headers=HEADERS,
            content=stream_frame(pcm) + struct.pack(">I", 0), request=req,
        )
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    assert local_tts_pcm_stream(
        "Neue Aufgabe nach Abbruch", "de", "neutral", lambda _: None,
    ) == pcm
    assert len(seen) == 2


def test_pcm_cancel_during_busy_does_not_retry_forever(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_TTS_ENDPOINT", "http://127.0.0.1:18190")
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE", "1.7B")
    state = {"calls": 0}
    def handler(req):
        state["calls"] += 1
        return httpx.Response(429, request=req)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr("omoi_mini.local_tts.time.sleep", lambda _: None)
    assert local_tts_pcm_stream(
        "Abgebrochener Satz.", "de", "neutral", lambda _: None,
        is_active=lambda: state["calls"] < 1,
    ) is None
    assert state["calls"] == 1
