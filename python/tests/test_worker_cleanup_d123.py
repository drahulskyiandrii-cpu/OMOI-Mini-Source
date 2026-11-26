"""Prozesshandles, Sprachgrenzen und lokale Umgebungen."""
import io
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.piper_tts import PiperTTSProcess
from omoi_mini.providers import ProviderFailure
from omoi_mini.runtime_config import runtime_environment


@pytest.mark.parametrize("kind",["stt","piper"])
def test_repeated_worker_exit_closes_pipes_and_threads(kind):
    engine=LocalWhisper() if kind=="stt" else PiperTTSProcess()
    for _ in range(5):
        proc=subprocess.Popen([sys.executable,"-u","-c",
            "import sys; print('ready',flush=True); sys.stdin.read()"],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            encoding="utf-8")
        engine.proc=proc
        reader=threading.Thread(target=lambda:proc.stdout.read(),daemon=True)
        writer=threading.Thread(target=lambda:proc.stdin.flush(),daemon=True)
        engine.reader,engine.writer=reader,writer
        reader.start();writer.start()
        try:engine.close()
        finally:
            if proc.poll() is None:proc.kill();proc.wait(5)
        assert proc.poll() is not None
        assert proc.stdin.closed and proc.stdout.closed
        assert not reader.is_alive() and not writer.is_alive()
        assert engine.proc is engine.reader is engine.writer is None


def test_worker_environment_does_not_inherit_cloud_secrets(monkeypatch):
    for key in ("OPENAI_API_KEY","CARTESIA_API_KEY","HF_TOKEN","GITHUB_TOKEN",
                "AWS_ACCESS_KEY_ID","OTHER_PASSWORD","CARTESIA_VOICE_ID"):
        monkeypatch.setenv(key,"synthetic")
    monkeypatch.setenv("OMOI_MINI_STT_MODEL","synthetic-model")
    result=runtime_environment()
    for key in ("OPENAI_API_KEY","CARTESIA_API_KEY","HF_TOKEN","GITHUB_TOKEN",
                "AWS_ACCESS_KEY_ID","OTHER_PASSWORD","CARTESIA_VOICE_ID"):
        assert key not in result
        assert os.environ[key]=="synthetic"
    assert result["OMOI_MINI_STT_MODEL"]=="synthetic-model"
    assert "PATH" in result


def test_clear_chat_failure_still_invalidates_recording_turn(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    turn=ctrl.next_turn()
    bridge=SimpleNamespace(recording=True,controller=ctrl,
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    def close_recording():bridge.recording=False
    bridge.close_recording=close_recording
    def fail():raise sqlite3.OperationalError("busy")
    monkeypatch.setattr(ctrl.store,"clear_messages",fail)
    try:
        Bridge.clearChat(bridge)
        assert not bridge.recording and not ctrl.current(turn)
        assert events[-1]["type"]=="error"
    finally:ctrl.close()


@pytest.mark.parametrize("language",["fr",None,42,{}])
def test_auto_requires_valid_detected_language(monkeypatch,language):
    worker=LocalWhisper("cuda")
    worker.proc=SimpleNamespace(stdin=True)
    monkeypatch.setattr(worker,"ensure",lambda *a,**k:None)
    monkeypatch.setattr(worker,"_request",lambda *a:{"ok":True,"text":"Hallo","language":language})
    with pytest.raises(ProviderFailure,match="unsicher"):
        worker.transcribe(b"synthetic","auto")
    assert worker.last_language is None


def test_detected_language_reset_after_invalid_reply(monkeypatch):
    worker=LocalWhisper("cuda")
    worker.proc=SimpleNamespace(stdin=True)
    monkeypatch.setattr(worker,"ensure",lambda *a,**k:None)
    reply={"ok":True,"text":"Hello","language":"en"}
    monkeypatch.setattr(worker,"_request",lambda *a:reply)
    assert worker.transcribe(b"synthetic","auto")=="Hello"
    assert worker.last_language=="en"
    with pytest.raises(ProviderFailure,match="andere Sprache"):
        worker.transcribe(b"synthetic","de")
    assert worker.last_language is None


def test_auto_uses_detected_language_for_domain_rules(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("stt","cuda")
    store.set_setting("stt_profile","balanced")
    store.set_setting("stt_auto_language",True)
    ctrl=Controller(store)
    calls=[]
    def transcribe(*a,**kw):
        ctrl.cuda_whisper.last_language="en"
        return "Anstellungen Spracherkennung"
    monkeypatch.setattr(ctrl.cuda_whisper,"transcribe",transcribe)
    monkeypatch.setattr(ctrl,"_dispatch_text",lambda turn,text,source:calls.append(text))
    try:
        ctrl._transcribe(ctrl.next_turn(),b"synthetic")
        assert calls==["Anstellungen Spracherkennung"]
    finally:ctrl.close()


@pytest.mark.parametrize("method",["_recording_level","_recording_partial"])
def test_invalid_settings_during_recording_abort_without_stt(method,tmp_path,monkeypatch):
    import time
    QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    turn=ctrl.next_turn()
    bridge=SimpleNamespace(recording=True,controller=ctrl,_recording_turn=turn,
        _recording_started=time.monotonic(),window=SimpleNamespace(store=ctrl.store),
        recorder=SimpleNamespace(signal_level=lambda:{"seconds":1,"rms":.02,"peak":.1}),
        uiEvent=SimpleNamespace(emit=lambda raw:events.append(json.loads(raw))))
    def close_recording():bridge.recording=False
    bridge.close_recording=close_recording
    def fail():raise sqlite3.OperationalError("busy")
    monkeypatch.setattr(ctrl.store,"get_settings",fail)
    monkeypatch.setattr(ctrl,"partial_audio",lambda *a:pytest.fail("STT request"))
    try:
        getattr(Bridge,method)(bridge)
        assert not bridge.recording and not ctrl.current(turn)
        assert events[-1]["type"]=="error"
    finally:ctrl.close()
