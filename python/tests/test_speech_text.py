"""Emoji-Filter: keine Modelle, kein Netzwerk, keine privaten Audiodaten."""
import json
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.speech_text import speech_text
from omoi_mini.recorder import Recorder


@pytest.mark.parametrize(("original","expected"), [
    ("Hallo \U0001f60a!", "Hallo!"),
    ("\u041f\u0440\u0438\u0432\u0435\u0442\U0001f44b\u043c\u0438\u0440", "\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440"),
    ("OK \U0001f469\U0001f3fd\u200d\U0001f4bb \U0001f1e9\U0001f1ea \u2764\ufe0f \u2705.", "OK."),
    ("1\ufe0f\u20e3 2\u20e3 #\ufe0f\u20e3 *\ufe0f\u20e3", ""),
    ("\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f!", ""),
    ("\U0001f60a \u2764\ufe0f!", ""),
    ("2 + 3 = 5; #1, *args, 20%, 10\u20ac, \u00a9 \u2122 \u2194", "2 + 3 = 5; #1, *args, 20%, 10\u20ac, \u00a9 \u2122 \u2194"),
    ("Wichtig\u203c", "Wichtig\u203c"),
    ("Info \u2139\ufe0f!", "Info!"),
])
def test_speech_text(original, expected):
    assert speech_text(original) == expected
    assert speech_text(expected) == expected


@pytest.mark.parametrize("provider", ["cartesia", "local", "piper"])
def test_provider_and_cache_use_filtered_text(tmp_path, monkeypatch, provider):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"speech.sqlite3")
    store.set_setting("tts",provider)
    store.set_setting("speak",True)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    ctrl=Controller(store)
    calls=[]
    def fake(text,*args,**kwargs):
        calls.append(text)
        return Recorder._as_wav(bytes(800),24000)
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",fake)
    monkeypatch.setattr("omoi_mini.controller.local_tts_wav",fake)
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:None)
    monkeypatch.setattr(ctrl.piper_voice,"synthesize",fake)
    try:
        ctrl._speak("\U0001f60a!", "de", 0)
        assert calls==[]
        ctrl._speak("Hallo \U0001f60a!", "de", 0)
        assert calls==["Hallo!"]
        assert ctrl._cache_path("Hallo \U0001f60a!","de",provider)==ctrl._cache_path("Hallo!","de",provider)
        ctrl._speak("Hallo!", "de", 0)
        assert calls==["Hallo!"]
    finally:
        ctrl.close()


def test_stream_split_emoji_kept_in_chat_but_not_speech(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"stream.sqlite3")
    store.set_setting("tts","cartesia")
    store.set_setting("llm","cloud")
    store.set_setting("speak",True)
    ctrl=Controller(store)
    events=[]
    submitted=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    pieces=["Hallo \U0001f469", "\U0001f3fd\u200d", "\U0001f4bb! ", "\U0001f60a"]
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",lambda *_:iter(pieces))
    monkeypatch.setattr(ctrl,"_submit_voice",lambda pool,turn,fn,text,*args,**kwargs:submitted.append(text))
    try:
        answer=ctrl._stream_llm(0,[],"de","cloud")
        assert answer=="".join(pieces)
        assert "".join(e["text"] for e in events if e["type"]=="stream_delta")==answer
        assert submitted==["Hallo!"]
        submitted.clear()
        monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",lambda *_:iter(["\U0001f60a", "\u2764", "\ufe0f!"]))
        ctrl._process(0,"Was meinst du?")
        assert submitted==[]
        assert store.messages()[-1]["content"]=="\U0001f60a\u2764\ufe0f!"
        assert any(e.get("avatarState")=="idle" for e in events)
    finally:
        ctrl.close()


def test_pcm_stream_receives_filtered_text(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"pcm.sqlite3")
    store.set_setting("tts","local")
    store.set_setting("speak",True)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    ctrl=Controller(store)
    calls=[]
    def pcm(text,lang,emotion,push_pcm,**kwargs):
        calls.append(text)
        data=b"\x00\x00"*8
        push_pcm(data)
        return data
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:None)
    monkeypatch.setattr("omoi_mini.controller.local_tts_default_speed",lambda *_:1.0)
    monkeypatch.setattr("omoi_mini.controller.local_tts_pcm_stream",pcm)
    try:
        ctrl._speak("Hallo \U0001f44b!", "de", 0, enqueue=True)
        assert calls==["Hallo!"]
        assert ctrl._cache_path("Hallo!","de","local").is_file()
    finally:
        ctrl.close()


def test_prepared_phrases_and_greeting_use_filtered_cache(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"phrases.sqlite3")
    store.set_setting("tts","cartesia")
    store.set_setting("speak",True)
    store.set_setting("greeting",True)
    monkeypatch.setattr(store,"phrase",lambda name,lang: {
        "hello":"Hallo \U0001f44b!", "introduce":"\U0001f60a!", "timer_end":"Fertig \u2705"
    }[name])
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    ctrl=Controller(store)
    calls=[]
    played=[]
    ctrl.audio_ready.connect(lambda path,turn:played.append(path))
    def fake(text,*args,**kwargs):
        calls.append(text)
        return Recorder._as_wav(bytes(800),24000)
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",fake)
    try:
        ctrl.startup_greeting()
        assert calls==played==[]
        ctrl._prepare_phrases_worker()
        assert calls==["Hallo!","Fertig"]
        ctrl.startup_greeting()
        assert played==[str(ctrl._cache_path("Hallo!","de","cartesia"))]
    finally:
        ctrl.close()
