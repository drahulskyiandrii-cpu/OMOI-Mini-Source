"""CPU-only: Mini darf lokale STT/TTS-Modelle nicht beim bloßen Start laden."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omoi_mini.core import Store
from omoi_mini import gui


class FakeBridge:
    def __init__(self, store):
        self.calls = []
        self.events = []
        self.window = SimpleNamespace(
            store=store,
            assets=SimpleNamespace(avatar=None),
            player=SimpleNamespace(
                prewarm_pcm=lambda: self.calls.append("pcm"),
            ),
        )
        self.controller = SimpleNamespace(
            change_turn_setting=store.set_setting,
            prewarm_cuda_stt=lambda: self.calls.append("cuda"),
            prewarm_cpu_stt=lambda: self.calls.append("cpu"),
            prewarm_local_tts=lambda: self.calls.append("tts"),
            startup_greeting=lambda: self.calls.append("greeting"),
        )
        self.uiEvent = SimpleNamespace(
            emit=lambda raw: self.events.append(json.loads(raw))
        )
        self.recording = False

    def _avatar_listing(self):
        return []

    def _avatar_index(self):
        return ""


@pytest.fixture
def isolated_bridge(monkeypatch, tmp_path):
    store = Store(tmp_path / "isolated.sqlite3")
    # Auch mit vorher bewusst gewählten CUDA-/Local-Anbietern kein Autostart.
    store.set_setting("stt", "cuda")
    store.set_setting("tts", "local")
    store.set_setting("llm", "local")
    bridge = FakeBridge(store)
    scheduled = []
    monkeypatch.setattr(
        gui, "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append(callback))
    )
    return bridge, scheduled


def test_opening_mini_never_prewarms_models_by_default(isolated_bridge):
    bridge, scheduled = isolated_bridge
    assert bridge.window.store.get_settings()["prewarm_local_models"] is False
    gui.Bridge.ready(bridge)
    assert bridge.calls == []
    assert len(scheduled) == 1  # nur vorhandenen Begrüßungs-Cache prüfen
    assert bridge.events[0]["type"] == "init"
    assert bridge.events[0]["settings"]["prewarm_local_models"] is False


def test_switching_providers_only_saves_configuration(isolated_bridge):
    bridge, scheduled = isolated_bridge
    gui.Bridge.saveSetting(bridge, "stt", json.dumps("cuda"))
    gui.Bridge.saveSetting(bridge, "tts", json.dumps("local"))
    gui.Bridge.saveSetting(bridge, "speak", json.dumps(True))
    assert bridge.calls == []
    assert scheduled == []


def test_legacy_prewarm_setting_no_longer_autostarts(isolated_bridge):
    bridge, scheduled = isolated_bridge
    store = bridge.window.store
    with pytest.raises(ValueError):
        store.set_setting("prewarm_local_models", "true")
    store.set_setting("prewarm_local_models", True)
    gui.Bridge.ready(bridge)
    assert bridge.calls == []
    assert len(scheduled) == 1  # Nur Begruessungscache; kein Modellstart
