"""Animation assignments persist per avatar; GUI confirmation and failure feedback.

Only synthetic data and isolated temporary SQLite databases; no user assets.
"""
from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from omoi_mini.core import Store, resolve_animation
from omoi_mini.gui import Bridge


def sample_vrma() -> bytes:
    doc = {
        "asset": {"version": "2.0"},
        "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0"}},
        "animations": [{
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
            "samplers": [{"input": 0, "output": 1}],
        }],
    }
    contents = json.dumps(doc).encode("utf-8")
    contents += b" " * (-len(contents) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(contents)) + (
        struct.pack("<I4s", len(contents), b"JSON") + contents
    )


class Collector:
    def __init__(self):
        self.events = []

    def emit(self, data):
        self.events.append(json.loads(data))


def make_stub(store: Store, avatar: Path, selected: Path | None):
    collector = Collector()
    staged = {"idle": selected} if selected else {}
    assets = SimpleNamespace(avatar=avatar, staged_animations=staged)
    stub = SimpleNamespace(
        window=SimpleNamespace(store=store, assets=assets),
        pending_names={"idle": "sample.vrma"} if selected else {},
        uiEvent=collector,
    )
    return stub, collector


def test_assignment_saved_after_confirm_and_reopened_per_avatar(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT",str(tmp_path/"empty-bundle"))
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path))
    avatar_a = tmp_path / "avatar-A.vrm"
    avatar_b = tmp_path / "avatar-B.vrm"
    avatar_a.write_bytes(b"avatar-A")
    avatar_b.write_bytes(b"avatar-B")
    animation = tmp_path / "selected.vrma"
    animation.write_bytes(sample_vrma())
    database = tmp_path / "omoi-mini.sqlite3"

    store = Store(database)
    bridge, sink = make_stub(store, avatar_a, animation)
    assert store.animations(str(avatar_a)) == {}, "Preview must not auto-save"
    Bridge.confirmAnimation(bridge, "idle")
    assert sink.events == [{
        "type": "animation_changed", "slot": "idle", "filename": "Benutzerdefiniert",
        "availableAlternatives": [], "availablePrimary": ["idle"],
    }]
    assert bridge.window.assets.staged_animations == {}
    assert store.animations(str(avatar_a)) == {"idle": str(animation)}
    assert resolve_animation("idle", str(avatar_a), store) == animation

    # Restart, select another avatar, then return: original assignment survives.
    reopened = Store(database)
    reopened.set_setting("avatar", str(avatar_b))
    assert reopened.animations(str(avatar_b)) == {}
    assert reopened.other_avatar_animation_count(str(avatar_b)) == 1
    assert reopened.animations(str(avatar_a)) == {"idle": str(animation)}
    assert reopened.other_avatar_animation_count(str(avatar_a)) == 0
    reopened.set_setting("avatar", str(avatar_a))
    reopened = Store(database)
    assert reopened.get_settings()["avatar"] == str(avatar_a)
    assert reopened.animations(str(avatar_a)) == {"idle": str(animation)}


def test_no_silent_success_without_preview(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3")
    avatar = tmp_path / "avatar.vrm"
    avatar.write_bytes(b"fake-avatar")
    bridge, sink = make_stub(store, avatar, None)
    Bridge.confirmAnimation(bridge, "idle")
    assert sink.events[-1]["type"] == "error"
    assert store.animations(str(avatar)) == {}


def test_sqlite_failure_does_not_discard_selected_preview(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "db.sqlite3")
    avatar = tmp_path / "avatar.vrm"
    avatar.write_bytes(b"fake-avatar")
    animation = tmp_path / "selected.vrma"
    animation.write_bytes(sample_vrma())
    bridge, sink = make_stub(store, avatar, animation)

    def fail(*_args):
        raise sqlite3.OperationalError("synthetic write failure")

    monkeypatch.setattr(store, "set_animation", fail)
    Bridge.confirmAnimation(bridge, "idle")
    assert sink.events[-1]["type"] == "error"
    assert "SQLite" in sink.events[-1]["value"]
    assert bridge.window.assets.staged_animations["idle"] == animation
    assert store.animations(str(avatar)) == {}
