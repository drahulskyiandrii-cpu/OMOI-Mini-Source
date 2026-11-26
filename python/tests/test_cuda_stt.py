"""CUDA STT routing + settings tests without GPU, downloads or network."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.local_stt import LocalWhisper


def test_cuda_stt_is_explicit_opt_in(tmp_path: Path):
    store=Store(tmp_path/"speech.sqlite3")
    assert store.get_settings()["stt"]=="cloud"
    store.set_setting("stt","cuda")
    assert Store(tmp_path/"speech.sqlite3").get_settings()["stt"]=="cuda"
    with pytest.raises(ValueError):
        store.set_setting("stt","cuda-or-cloud-fallback")


def test_cuda_worker_path_and_backend_are_selected():
    worker=LocalWhisper(engine="cuda")
    assert worker.engine=="cuda"
    assert worker.worker.name=="cuda_worker.py"
    worker.close()
    default=LocalWhisper()
    assert default.engine=="openvino"
    assert default.worker.name=="worker.mjs"
    default.close()


def test_controller_cuda_route_never_uses_cloud_or_openvino(tmp_path: Path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"speech.sqlite3")
    store.set_setting("stt","cuda")
    store.set_setting("speak",False)
    controller=Controller(store)
    called=[]
    def cuda(data,language,**kwargs):
        called.append((data,language))
        return "ОМои Mini привет"
    def fail(*_):
        raise AssertionError("Wrong STT engine")
    monkeypatch.setattr(controller.cuda_whisper,"transcribe",cuda)
    monkeypatch.setattr(controller.whisper,"transcribe",fail)
    monkeypatch.setattr("omoi_mini.controller.transcribe_openai",fail)
    monkeypatch.setattr(controller,"_dispatch_text",lambda turn,text,source:called.append((text,source)))
    try:
        turn=controller.next_turn()
        store.set_setting("language","ru")
        controller._transcribe(turn,b"fake-test-audio")
        assert called==[
            (b"fake-test-audio","ru"),
            ("OMOI Mini привет","microphone"),
        ]
    finally:
        controller.close()


def test_cuda_stack_prewarm_only_when_selected(tmp_path: Path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"speech.sqlite3")
    controller=Controller(store)
    calls=[]
    monkeypatch.setattr(controller.cuda_whisper,"ensure",lambda *a,**kw:calls.append("cuda"))
    try:
        assert controller._stack_tasks(store.get_settings())==[]
        store.set_setting("stt","cuda")
        tasks=controller._stack_tasks(store.get_settings())
        assert [name for name,_ in tasks]==["STT"]
        tasks[0][1]()
        assert calls==["cuda"]
    finally:
        controller.close()
