"""Piper-Stimmauswahl, Audio-Cache und Cloud-isolierte Wiedergabe."""
import io
import wave
from pathlib import Path
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.piper_tts import PiperTTSProcess
from omoi_mini.providers import ProviderFailure


def fake_wav() -> bytes:
    target=io.BytesIO()
    with wave.open(target,"wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(16000)
        sound.writeframes(b"\x00\x00"*400)
    return target.getvalue()


def test_piper_voices_are_validated_before_worker_start():
    worker=PiperTTSProcess()
    with pytest.raises(ProviderFailure):
        worker.synthesize("Hallo","de","ru_RU-irina-medium")
    with pytest.raises(ProviderFailure):
        worker.synthesize("x"*1801,"de","de_DE-ramona-low")
    assert worker.proc is None


def test_piper_voice_settings_are_limited(tmp_path:Path):
    store=Store(tmp_path/"settings.sqlite3")
    store.set_setting("tts","piper")
    store.set_setting("piper_voice_de","de_DE-eva_k-x_low")
    assert store.get_settings()["piper_voice_de"]=="de_DE-eva_k-x_low"
    with pytest.raises(ValueError):
        store.set_setting("piper_voice_de","unknown")
    with pytest.raises(ValueError):
        store.set_setting("piper_voice_ru","de_DE-ramona-low")


def test_piper_controller_cache_is_voice_specific(tmp_path:Path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"piper.sqlite3")
    store.set_setting("tts","piper")
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    ctrl=Controller(store)
    audio=[]
    voices=[]
    ctrl.audio_ready.connect(lambda path,turn:audio.append(path))
    def synthesize(text,lang,voice,**kw):
        voices.append((lang,voice))
        return fake_wav()
    monkeypatch.setattr(ctrl.piper_voice,"synthesize",synthesize)
    try:
        first=ctrl._cache_path("Hallo","de","piper")
        ctrl._speak("Hallo","de",None)
        assert len(audio)==1
        assert voices==[("de","de_DE-ramona-low")]
        assert first.is_file()
        ctrl._speak("Hallo","de",None)
        assert len(voices)==1
        store.set_setting("piper_voice_de","de_DE-eva_k-x_low")
        second=ctrl._cache_path("Hallo","de","piper")
        assert first!=second
        ctrl._speak("Hallo","de",None)
        assert voices[-1]==("de","de_DE-eva_k-x_low")
        assert second.is_file()
    finally:
        ctrl.close()
