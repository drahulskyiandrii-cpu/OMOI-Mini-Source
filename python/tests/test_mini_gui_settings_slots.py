"""Isolierte Qt-WebChannel-Slots: echte Mini-SQLite, keine Fenster oder Modelle."""
from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

from omoi_mini import gui
from omoi_mini.core import Store


def fake_bridge(store: Store):
    events = []
    bridge = SimpleNamespace(
        window=SimpleNamespace(
            store=store,
            assets=SimpleNamespace(avatar=None),
        ),
        uiEvent=SimpleNamespace(emit=lambda raw: events.append(json.loads(raw))),
        recording=False,
        controller=SimpleNamespace(change_turn_setting=store.set_setting),
    )
    return bridge, events


def test_local_gguf_and_llama_path_buttons_persist_without_launch(monkeypatch, tmp_path):
    database = tmp_path / "mini.sqlite3"
    store = Store(database)
    bridge, events = fake_bridge(store)
    gguf = tmp_path / "user-model.gguf"
    server = tmp_path / "llama-server.exe"
    gguf.write_bytes(b"TEST ONLY - NO MODEL")
    server.write_bytes(b"TEST ONLY - NO EXECUTABLE")
    selected = iter([str(gguf), str(server)])
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (next(selected), ""))
    gui.Bridge.chooseLocalGGUF(bridge)
    gui.Bridge.chooseLlamaServer(bridge)
    reopened = Store(database).get_settings()
    assert reopened["llm_model_file"] == str(gguf.resolve())
    assert reopened["llm_server_file"] == str(server.resolve())
    assert [event["type"] for event in events] == ["notice", "notice"]


def test_own_voice_button_imports_only_local_wav(monkeypatch, tmp_path):
    profile = tmp_path / "omoi-mini-profile"
    monkeypatch.setenv("OMOI_MINI_DATA_DIR", str(profile))
    original = tmp_path / "allowed-test-voice.wav"
    with wave.open(str(original), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * (4 * 16000))
    before = original.read_bytes()
    database = profile / "mini.sqlite3"
    bridge, events = fake_bridge(Store(database))
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(original), ""))
    gui.Bridge.selectVoiceReference(bridge)
    imported = Path(Store(database).get_settings()["qwen_voice_reference"])
    assert imported.parent == profile / "voices"
    assert imported.read_bytes() == before == original.read_bytes()
    assert [event["type"] for event in events] == ["notice"]


def test_cancelled_or_invalid_dialog_does_not_change_settings(monkeypatch, tmp_path):
    bridge, events = fake_bridge(Store(tmp_path / "mini.sqlite3"))
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: ("", ""))
    gui.Bridge.chooseLocalGGUF(bridge)
    gui.Bridge.selectVoiceReference(bridge)
    assert not events
    invalid = tmp_path / "not-a-model.txt"
    invalid.write_text("not a gguf")
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(invalid), ""))
    gui.Bridge.chooseLocalGGUF(bridge)
    assert events[-1]["type"] == "error"
    assert bridge.window.store.get_settings()["llm_model_file"] == ""
    assert bridge.window.store.get_settings()["qwen_voice_reference"] == ""


def test_webchannel_custom_text_saved_and_restored(monkeypatch, tmp_path):
    database = tmp_path / "mini.sqlite3"
    store = Store(database)
    bridge, events = fake_bridge(store)
    gui.Bridge.saveSetting(bridge, "hello_de", json.dumps("Guten Tag, das ist Mini."))
    gui.Bridge.saveSetting(bridge, "hello_ru", json.dumps("Привет, это Мини."))
    reopened = Store(database)
    assert reopened.phrase("hello", "de") == "Guten Tag, das ist Mini."
    assert reopened.phrase("hello", "ru") == "Привет, это Мини."
    gui.Bridge.saveSetting(bridge, "hello_de", json.dumps(""))
    assert reopened.phrase("hello", "de") != "Guten Tag, das ist Mini."
    assert [event["type"] for event in events] == ["setting", "setting", "setting"]


def test_mouse_tracking_switch_and_cursor_are_local(monkeypatch,tmp_path):
    from PySide6.QtCore import QPoint
    store=Store(tmp_path/"db.sqlite")
    bridge,events=fake_bridge(store)
    assert store.get_settings()["mouse_tracking"] is True
    gui.Bridge.saveSetting(bridge,"mouse_tracking","false")
    assert Store(store.path).get_settings()["mouse_tracking"] is False
    gui.Bridge.saveSetting(bridge,"mouse_tracking",'"invalid"')
    assert store.get_settings()["mouse_tracking"] is False
    assert events[-2]["type"]=="setting" and events[-2]["value"] is False
    assert events[-1]["type"]=="error"
    monkeypatch.setattr(gui,"QCursor",SimpleNamespace(pos=lambda:QPoint(2000,-2000)))
    bridge.window.isVisible=lambda:True
    bridge.window.isMinimized=lambda:False
    bridge.window.view=SimpleNamespace(mapFromGlobal=lambda point:point,width=lambda:100,height=lambda:200)
    assert gui.Bridge.cursorPosition(bridge)==[300,-400]
    bridge.window.isMinimized=lambda:True
    assert gui.Bridge.cursorPosition(bridge)==[]
