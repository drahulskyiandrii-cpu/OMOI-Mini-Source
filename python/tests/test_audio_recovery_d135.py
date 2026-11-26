"""FIFO-Fehlerpfade und Freigabe-Retry ohne Audiogeraete."""
from collections import deque
from types import SimpleNamespace
import json
import sqlite3
import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.audio import AudioPlayer
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge
from omoi_mini.recorder import Recorder


def test_many_broken_wav_files_are_skipped_without_recursion(tmp_path):
    path=tmp_path/"missing.wav"
    events=[]
    fake=SimpleNamespace(queue=deque([("file",str(path))]*1500),_active=True,
        audio_samples=np.ones(1000),event=SimpleNamespace(emit=events.append),
        _emit_state=lambda active:events.append(active))
    fake._play_now=lambda name:AudioPlayer._play_now(fake,name)
    AudioPlayer._play_next(fake)
    assert not fake.queue and not fake._active and fake.audio_samples.size==0
    assert events[-1] is False
    assert len(events)==1502


def test_sync_media_error_during_set_source_does_not_recurse(tmp_path):
    path=tmp_path/"valid.wav"
    path.write_bytes(Recorder._as_wav(bytes(800),24000))
    events=[]
    fake=SimpleNamespace(_active=False,_starting_file=False,_file_start_failed=False,
        _pcm_token=None,event=SimpleNamespace(emit=events.append),
        _play_next=lambda:pytest.fail("reentrant next"),
        _emit_state=lambda value:pytest.fail("failed playback marked active"))
    fake.player=SimpleNamespace(setSource=lambda *a:AudioPlayer._on_error(fake,None,"synthetic"),
                               play=lambda:pytest.fail("play after error"))
    assert AudioPlayer._play_now(fake,str(path)) is False
    assert not fake._starting_file and not fake._active and len(events)==1


def test_stop_does_not_schedule_device_reopen(monkeypatch):
    calls=[]
    monkeypatch.setattr("omoi_mini.audio.QTimer",SimpleNamespace(singleShot=lambda *a:pytest.fail("deferred open")))
    fake=SimpleNamespace(_latest_turn=1,_pcm_token="active",stop=lambda **kw:calls.append(kw))
    AudioPlayer.stop_for_turn(fake,2)
    assert fake._latest_turn==2 and calls==[{"reset_device":True}]


def test_failed_unload_retried_on_later_change(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    ctrl.store.set_setting("tts","local")
    jobs=[]
    monkeypatch.setattr(ctrl.tts_pool,"submit",lambda fn,*a:jobs.append((fn,a)))
    calls=[]
    def unload(unused):
        assert unused()
        calls.append(True)
        if len(calls)==1:raise OSError("synthetic")
    monkeypatch.setattr(ctrl.local_voice,"unload_if",unload)
    try:
        ctrl.change_turn_setting("tts","cartesia")
        fn,args=jobs.pop();fn(*args)
        assert ctrl._release_retry=={"qwen"}
        ctrl.change_turn_setting("language","ru")
        fn,args=jobs.pop();fn(*args)
        assert len(calls)==2 and not ctrl._release_retry
    finally:monkeypatch.undo();ctrl.close()


def test_failed_settings_save_restores_authoritative_value(tmp_path,monkeypatch):
    store=Store(tmp_path/"db")
    events=[]
    def fail(*args):raise sqlite3.OperationalError("private location")
    bridge=SimpleNamespace(window=SimpleNamespace(store=store),recording=False,
        controller=SimpleNamespace(change_turn_setting=fail),
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    Bridge.saveSetting(bridge,"speak","false")
    assert events[0]=={"type":"setting","key":"speak","value":True}
    assert events[1]["type"]=="error" and "private location" not in str(events)
