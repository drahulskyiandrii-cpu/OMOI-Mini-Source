"""Beschaedigten Cache regenerieren, nicht automatisch Cloud ausloesen."""
import json
import os
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.recorder import Recorder
from omoi_mini.audio_cache import cache_ready,prepare_cache_target


@pytest.fixture
def ctrl(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    c=Controller(Store(tmp_path/"db"))
    try:yield c
    finally:c.close()


def test_corrupt_cache_regenerated_only_on_explicit_speech(ctrl,monkeypatch):
    calls,played=[],[]
    wav=Recorder._as_wav(bytes(800),24000)
    path=ctrl._cache_path("Hallo","de","cartesia")
    path.parent.mkdir()
    path.write_bytes(wav[:-2])
    def synth(*a,**kw):calls.append(True);return wav
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",synth)
    ctrl.audio_ready.connect(lambda p,t:played.append(p))
    ctrl._speak("Hallo","de",0)
    ctrl._speak("Hallo","de",0)
    assert calls==[True] and played==[str(path)]*2
    assert path.read_bytes()==wav and cache_ready(path)


def test_corrupt_greeting_never_triggers_synthesis(ctrl,monkeypatch):
    ctrl.store.set_setting("hello_de","Hallo")
    path=ctrl._cache_path("Hallo","de","cartesia")
    path.parent.mkdir()
    path.write_bytes(b"invalid")
    played=[]
    ctrl.audio_ready.connect(lambda *a:played.append(a))
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",lambda *a,**kw:pytest.fail("startup API"))
    ctrl.startup_greeting()
    assert not played and path.read_bytes()==b"invalid"


def test_cache_path_is_not_a_directory(ctrl,monkeypatch):
    path=ctrl._cache_path("Hallo","de","cartesia")
    path.mkdir(parents=True)
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",lambda *a,**kw:pytest.fail("invalid target"))
    ctrl._speak("Hallo","de",0)
    assert path.is_dir()


def test_redirected_cache_directory_not_used(tmp_path):
    target=tmp_path/"other"
    target.mkdir()
    root=tmp_path/"audio-cache"
    try:root.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip("Symlink creation unavailable")
    path=root/("a"*64+".wav")
    (target/path.name).write_bytes(Recorder._as_wav(bytes(800),24000))
    assert not cache_ready(path)
    with pytest.raises(OSError):prepare_cache_target(path)
    assert (target/path.name).exists()
