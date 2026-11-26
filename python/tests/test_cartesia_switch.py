"""D78: Cartesia settings and provider switching, no GPU or real HTTP."""
import json
import io
import wave
import struct
import sys
from types import SimpleNamespace

import httpx
import pytest

from omoi_mini import gui, providers, controller
from omoi_mini.core import Store


def test_mini_voice_takes_precedence_and_is_independent(monkeypatch,tmp_path):
    store=Store(tmp_path/"mini.sqlite3")
    store.set_setting("cartesia_voice_id","private-test-id")
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    monkeypatch.setenv("CARTESIA_VOICE_ID","environment-test")
    monkeypatch.setenv("CARTESIA_API_KEY","fake-key")
    monkeypatch.setattr(providers,"omoi_model_root",lambda:pytest.fail("Legacy lookup"))
    assert providers.nika_voice_id(store.get_settings()["cartesia_voice_id"])=="private-test-id"
    assert providers.cartesia_voice_status("private-test-id")=="mini"
    assert providers.nika_voice_id()=="environment-test"
    monkeypatch.delenv("CARTESIA_API_KEY")
    assert providers.cartesia_voice_status("private-test-id")=="key_missing"


def placeholder_wav():
    data=io.BytesIO()
    with wave.open(data,"wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(b"\x01\x00"*16)
    raw=bytearray(data.getvalue())
    struct.pack_into("<I",raw,4,0xffffffff)
    struct.pack_into("<I",raw,40,0xffffffff)
    return bytes(raw)


def test_cloud_streaming_wav_header_is_normalized():
    data=providers._normalize_cartesia_wav(placeholder_wav())
    with wave.open(io.BytesIO(data),"rb") as f:
        assert f.getnframes()==16
        assert f.readframes(16)==b"\x01\x00"*16


def test_cloud_request_uses_configured_voice(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY","fake-key")
    seen=[]
    real=httpx.AsyncClient
    def response(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200,content=placeholder_wav())
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(response),**kw))
    assert providers.cartesia_wav("Hallo","de",voice_id="private-test-id").startswith(b"RIFF")
    assert seen[0]["voice"]=="private-test-id"
    assert seen[0]["language"]=="de"


def test_provider_switch_and_voice_save_without_model_start(tmp_path,monkeypatch):
    store=Store(tmp_path/"mini.sqlite3")
    calls=[]
    events=[]
    monkeypatch.setenv("CARTESIA_API_KEY","fake-key")
    def change(key,value):
        store.set_setting(key,value)
        calls.append("cancel")
    bridge=SimpleNamespace(window=SimpleNamespace(store=store),recording=False,
        controller=SimpleNamespace(change_turn_setting=change),
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    gui.Bridge.saveSetting(bridge,"tts",json.dumps("local"))
    gui.Bridge.saveSetting(bridge,"tts",json.dumps("cartesia"))
    assert store.get_settings()["tts"]=="cartesia"
    gui.Bridge.saveCartesiaVoice(bridge,"test-private-id")
    assert store.get_settings()["cartesia_voice_id"]=="test-private-id"
    assert events[-1]=={"type":"cartesia_voice_status","value":"mini"}
    assert calls==["cancel","cancel","cancel"]
    assert "test-private-id" not in json.dumps(events)
    gui.Bridge.saveSetting(bridge,"cartesia_voice_id",json.dumps("untrusted"))
    assert store.get_settings()["cartesia_voice_id"]=="test-private-id"
    gui.Bridge.saveCartesiaVoice(bridge,"bad id")
    assert events[-1]["type"]=="error"
    assert store.get_settings()["cartesia_voice_id"]=="test-private-id"


def test_existing_controller_uses_new_provider_and_voice_without_restart(monkeypatch,tmp_path):
    monkeypatch.setattr(controller,"user_data_dir",lambda:tmp_path)
    monkeypatch.delenv("CARTESIA_VOICE_ID",raising=False)
    store=Store(tmp_path/"mini.sqlite3")
    ctrl=controller.Controller(store)
    calls=[]
    try:
        monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:calls.append("local"))
        monkeypatch.setattr(controller,"local_tts_wav",lambda *a,**kw:b"local-audio")
        monkeypatch.setattr(controller,"cartesia_wav",lambda *a,**kw:calls.append(kw["voice_id"]) or b"cloud-audio")
        store.set_setting("tts","local")
        ctrl._speak("hello","de",ctrl.next_turn())
        store.set_setting("tts","cartesia")
        store.set_setting("cartesia_voice_id","voice-a")
        first=ctrl._cache_path("hello","de","cartesia")
        ctrl._speak("hello","de",ctrl.next_turn())
        store.set_setting("cartesia_voice_id","voice-b")
        second=ctrl._cache_path("hello","de","cartesia")
        ctrl._speak("hello","de",ctrl.next_turn())
        assert calls==["local","voice-a","voice-b"]
        assert first!=second
        assert first.read_bytes()==second.read_bytes()==b"cloud-audio"
        store.set_setting("speak",False)
        ctrl._speak("no audio","de",ctrl.next_turn())
        assert calls==["local","voice-a","voice-b"]
    finally:
        ctrl.close()
