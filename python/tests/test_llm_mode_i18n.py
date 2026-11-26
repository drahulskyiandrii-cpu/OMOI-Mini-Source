"""D95: persisted interface language and explicit model selection; no inference."""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.gui import Bridge
from omoi_mini.providers import LocalGemma, ProviderFailure


def test_mode_legacy_and_roundtrip(tmp_path):
    store = Store(tmp_path/"db")
    assert store.get_settings()["llm_mode"] == "cloud"
    store.set_setting("llm", "local")
    assert store.get_settings()["llm_mode"] == "local_standard"
    store.set_setting("llm_local_profile", "gemma12")
    assert store.get_settings()["llm_mode"] == "local_12b"
    for mode in ("cloud","local_standard","local_12b","cloud"):
        store.set_setting("llm_mode",mode)
        assert Store(store.path).get_settings()["llm_mode"] == mode
    with pytest.raises(ValueError):
        store.set_setting("llm_mode","invalid")


def test_language_independent(tmp_path):
    store = Store(tmp_path/"db")
    store.set_setting("ui_language","ru")
    assert Store(store.path).get_settings()["ui_language"] == "ru"
    assert store.get_settings()["language"] == "de"
    store.set_setting("language","ru")
    store.set_setting("ui_language","de")
    assert store.get_settings()["language"] == "ru"
    with pytest.raises(ValueError):
        store.set_setting("ui_language","en")


def test_restart_guard_and_file_status(tmp_path,monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    store = Store(tmp_path/"db")
    model = tmp_path/"small.gguf"
    server = tmp_path/"llama-server.exe"
    model.write_bytes(b"GGUF")
    server.write_bytes(b"MZ")
    store.set_setting("llm_model_file",str(model))
    store.set_setting("llm_server_file",str(server))
    store.set_setting("llm_mode","local_standard")
    ctrl = Controller(store)
    fake = SimpleNamespace(controller=ctrl,window=SimpleNamespace(store=store))
    try:
        report = Bridge._llm_status(fake)
        assert report["standardModel"] and report["standardRuntime"]
        assert not report["restartRequired"]
        assert str(tmp_path) not in json.dumps(report)
        monkeypatch.setattr(ctrl.gemma,"stream_answer",lambda *a:pytest.fail("inference"))
        store.set_setting("llm_mode","local_12b")
        assert Bridge._llm_status(fake)["restartRequired"]
        with pytest.raises(ProviderFailure,match="neu starten"):
            ctrl._stream_llm(ctrl.next_turn(),[],"de","local")
        store.set_setting("llm_mode","local_standard")
        store.set_setting("llm_model_file","")
        assert Bridge._llm_status(fake)["restartRequired"]
        with pytest.raises(ProviderFailure,match="neu starten"):
            ctrl._stream_llm(ctrl.next_turn(),[],"de","local")
    finally:
        ctrl.close()


def test_frozen_standard_rejects_foreign_llm(monkeypatch):
    import sys
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    engine = LocalGemma("small.gguf","llama-server.exe")
    monkeypatch.setattr(engine,"_available",lambda:True)
    with pytest.raises(ProviderFailure,match="Port belegt"):
        engine.ensure()
