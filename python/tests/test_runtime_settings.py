"""SQLite-Laufzeitpfade und native Slots ohne Fenster, Netzwerk oder Inferenz."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from omoi_mini import gui, local_stt, local_tts, piper_tts
from omoi_mini.core import Store
from omoi_mini.runtime_config import RUNTIME_SETTINGS, runtime_overrides, runtime_environment
from omoi_mini.runtime_diagnostics import inspect_local_runtimes
from omoi_mini.providers import ProviderFailure


def bridge_for(store):
    events = []
    bridge = SimpleNamespace(window=SimpleNamespace(store=store),recording=False,
                             controller=SimpleNamespace(change_turn_setting=store.set_setting),
                             uiEvent=SimpleNamespace(emit=lambda s: events.append(json.loads(s))))
    return bridge, events


def test_sqlite_defaults_overrides_reset_and_isolation(tmp_path, monkeypatch):
    key = "runtime_cuda_model"
    env_key = RUNTIME_SETTINGS[key]
    monkeypatch.setenv(env_key, str(tmp_path / "old"))
    db = tmp_path / "settings.sqlite"
    store = Store(db)
    assert all(store.get_settings()[key] == "" for key in RUNTIME_SETTINGS)
    selected = str(tmp_path / "new")
    store.set_setting(key, selected)
    overrides = runtime_overrides(Store(db).get_settings())
    assert runtime_environment(overrides)[env_key] == selected
    assert os.environ[env_key] == str(tmp_path / "old")
    store.set_setting(key, "")
    assert runtime_environment(runtime_overrides(store.get_settings()))[env_key] == os.environ[env_key]


@pytest.mark.parametrize("value", [123, "relative", "x" * 1025, "\x00"])
def test_store_rejects_invalid_paths(tmp_path, value):
    with pytest.raises(ValueError):
        Store(tmp_path / "db").set_setting("runtime_root", value)


@pytest.mark.parametrize("key", list(RUNTIME_SETTINGS))
def test_native_picker_persistence_and_reset(tmp_path, monkeypatch, key):
    store = Store(tmp_path / "db")
    bridge, events = bridge_for(store)
    target = tmp_path / "python.exe" if key.endswith("_python") else tmp_path / "component"
    if key.endswith("_python"):
        target.write_bytes(b"NOT EXECUTABLE")
        monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *_: (str(target), ""))
    else:
        target.mkdir()
        monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *_: str(target))
    monkeypatch.delenv(RUNTIME_SETTINGS[key], raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Process start"))
    gui.Bridge.chooseRuntimePath(bridge, key)
    assert Store(store.path).get_settings()[key] == str(target.resolve())
    assert events[-1]["sources"][key] == "mini"
    assert str(target) not in str(events)
    gui.Bridge.resetRuntimePath(bridge, key)
    assert Store(store.path).get_settings()[key] == ""
    assert events[-1]["sources"][key] == "default"


def test_cancel_invalid_and_non_native_path_change(tmp_path, monkeypatch):
    store = Store(tmp_path / "db")
    bridge, events = bridge_for(store)
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *_: "")
    gui.Bridge.chooseRuntimePath(bridge, "runtime_root")
    assert not events
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *_: (str(tmp_path / "wrong.exe"), ""))
    gui.Bridge.chooseRuntimePath(bridge, "runtime_qwen_python")
    gui.Bridge.saveSetting(bridge, "runtime_root", json.dumps(str(tmp_path)))
    assert [e["type"] for e in events] == ["error", "error"]
    assert not runtime_overrides(store.get_settings())


def test_saved_paths_diagnosed_without_launch(tmp_path, monkeypatch):
    root = tmp_path / "private-workers"
    (root / "local-stt").mkdir(parents=True)
    (root / "local-stt" / "cuda_worker.py").write_text("# test")
    store = Store(tmp_path / "db")
    store.set_setting("runtime_root", str(root))
    bridge, events = bridge_for(store)
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT", str(tmp_path / "other"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Process start"))
    gui.Bridge.diagnoseRuntimePaths(bridge)
    assert events[-1]["type"] == "runtime_diagnosis"
    assert events[-1]["report"]["cuda_stt"]["worker_file"]
    assert str(root) not in str(events)


def test_adapters_use_sqlite_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "workers"
    store = Store(tmp_path / "db")
    store.set_setting("runtime_root", str(root))
    for key in ("runtime_cuda_python", "runtime_qwen_python", "runtime_piper_python"):
        exe = tmp_path / key / "python.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"FAKE")
        store.set_setting(key, str(exe))
    for key in ("runtime_cuda_overlay", "runtime_cuda_model"):
        folder = tmp_path / key
        folder.mkdir()
        store.set_setting(key, str(folder))
    (tmp_path / "runtime_cuda_model" / "model.bin").write_bytes(b"FAKE")
    for name in ("local-stt/cuda_worker.py", "local-tts/qwen_server.py", "local-tts/piper_worker.py"):
        worker = root / name
        worker.parent.mkdir(parents=True, exist_ok=True)
        worker.write_text("# no execution")
    overrides = runtime_overrides(store.get_settings())
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT", str(tmp_path / "wrong"))
    calls = []
    def fake_popen(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError("synthetic")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    stt = local_stt.LocalWhisper("cuda", runtime_overrides=overrides)
    qwen = local_tts.LocalTTSProcess(runtime_overrides=overrides)
    monkeypatch.setattr(qwen, "_ready", lambda *_: False)
    piper = piper_tts.PiperTTSProcess(runtime_overrides=overrides)
    store.set_setting("runtime_root", str(tmp_path / "new"))
    with pytest.raises(ProviderFailure):
        stt.ensure()
    with pytest.raises(ProviderFailure,match="Qwen-Python-Laufzeit"):
        qwen.ensure()
    with pytest.raises(FileNotFoundError):
        piper._ensure()
    assert calls == [
        [str(tmp_path / "runtime_cuda_python" / "python.exe"), str(root / "local-stt/cuda_worker.py")],
        [str(tmp_path / "runtime_qwen_python" / "python.exe"), str(root / "local-tts/qwen_server.py")],
        [str(tmp_path / "runtime_piper_python" / "python.exe"), str(root / "local-tts/piper_worker.py")],
    ]
