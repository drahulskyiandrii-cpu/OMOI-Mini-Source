"""Robuste Pfadauflösung für separate Mini-Worker in Python- und EXE-Modus."""
import sys
from pathlib import Path

from omoi_mini.core import external_runtime_root


def test_development_worker_root(monkeypatch):
    monkeypatch.delenv("OMOI_MINI_RUNTIME_ROOT", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    root = external_runtime_root()
    assert (root / "local-stt" / "worker.mjs").is_file()
    assert (root / "local-tts" / "qwen_server.py").is_file()
    assert (root / "local-tts" / "piper_worker.py").is_file()


def test_explicit_runtime_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT", str(tmp_path))
    assert external_runtime_root() == tmp_path.resolve()


def test_frozen_exe_finds_nearby_checkout(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("OMOI_MINI_RUNTIME_ROOT", raising=False)
    root = tmp_path / "project"
    worker = root / "local-stt" / "worker.mjs"
    worker.parent.mkdir(parents=True)
    worker.write_text("// test")
    exe = root / "dist" / "windows" / "OMOI-Mini" / "OMOI-Mini.exe"
    exe.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert external_runtime_root() == root.resolve()


def test_frozen_exe_without_external_workers_fails_safe(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("OMOI_MINI_RUNTIME_ROOT", raising=False)
    exe = tmp_path / "OMOI-Mini.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert external_runtime_root() == tmp_path.resolve()
