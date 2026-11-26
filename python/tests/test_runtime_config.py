"""Explizite Worker-Laufzeitpfade ohne echten Prozess- oder Modellstart."""
from pathlib import Path

import pytest

from omoi_mini.runtime_config import runtime_paths
from omoi_mini.runtime_diagnostics import inspect_local_runtimes
from omoi_mini import local_stt, local_tts, piper_tts
from omoi_mini.providers import ProviderFailure


def test_defaults_are_compatible_and_do_not_create_directories(tmp_path):
    base = tmp_path / "not-created"
    paths = runtime_paths({}, base)
    assert paths.cuda_python == paths.qwen_python
    assert paths.cuda_python == base / "tts-eval/qwen-venv/Scripts/python.exe"
    assert paths.cuda_overlay == base / "stt-eval/faster-whisper-overlay"
    assert paths.cuda_model == base / "stt-eval/faster-whisper-small-ct2"
    assert not base.exists()


def independent_paths(monkeypatch, tmp_path):
    selected = {
        "OMOI_MINI_CUDA_STT_PYTHON": tmp_path / "stt-env/python.exe",
        "OMOI_MINI_CUDA_STT_OVERLAY": tmp_path / "stt-overlay",
        "OMOI_MINI_CUDA_STT_MODEL": tmp_path / "ct2",
        "OMOI_MINI_QWEN_PYTHON": tmp_path / "qwen-env/python.exe",
        "OMOI_MINI_PIPER_PYTHON": tmp_path / "piper-env/python.exe",
    }
    for key, value in selected.items():
        monkeypatch.setenv(key, str(value))
        if value.suffix == ".exe":
            value.parent.mkdir(parents=True)
            value.write_bytes(b"FAKE_NOT_EXECUTABLE")
        else:
            value.mkdir()
    (selected["OMOI_MINI_CUDA_STT_MODEL"] / "model.bin").write_bytes(b"FAKE")
    return selected


def test_explicit_paths_independent_and_diagnosis_matches(monkeypatch, tmp_path):
    selected = independent_paths(monkeypatch, tmp_path)
    root = tmp_path / "workers"
    for name in ("local-stt/cuda_worker.py", "local-tts/qwen_server.py",
                 "local-tts/piper_worker.py"):
        worker = root / name
        worker.parent.mkdir(parents=True, exist_ok=True)
        worker.write_text("# synthetic")
    paths = runtime_paths()
    assert paths.cuda_python != paths.qwen_python
    assert paths.cuda_python == selected["OMOI_MINI_CUDA_STT_PYTHON"]
    assert paths.qwen_python == selected["OMOI_MINI_QWEN_PYTHON"]
    status = inspect_local_runtimes(root, tmp_path / "unused", node_found=False)
    for key in ("cuda_stt", "qwen_tts", "piper_tts"):
        assert all(status[key].values())
    assert not (tmp_path / "unused").exists()


def test_cuda_passes_checked_paths_to_child_without_start(monkeypatch, tmp_path):
    selected = independent_paths(monkeypatch, tmp_path)
    stt = local_stt.LocalWhisper("cuda")
    stt.worker = tmp_path / "cuda_worker.py"
    stt.worker.write_text("# no execution")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs["env"]))
        raise FileNotFoundError("synthetic")

    monkeypatch.setattr(local_stt.subprocess, "Popen", fake_popen)
    with pytest.raises(ProviderFailure, match="Python-Laufzeit"):
        stt.ensure()
    command, env = calls[0]
    assert command[0] == str(selected["OMOI_MINI_CUDA_STT_PYTHON"])
    for key in ("OMOI_MINI_CUDA_STT_MODEL", "OMOI_MINI_CUDA_STT_OVERLAY"):
        assert env[key] == str(selected[key])
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def test_missing_explicit_cuda_model_fails_before_spawn(monkeypatch, tmp_path):
    selected = independent_paths(monkeypatch, tmp_path)
    (selected["OMOI_MINI_CUDA_STT_MODEL"] / "model.bin").unlink()
    stt = local_stt.LocalWhisper("cuda")
    stt.worker = tmp_path / "cuda_worker.py"
    stt.worker.write_text("# no execution")
    monkeypatch.setattr(local_stt.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("worker started"))
    with pytest.raises(ProviderFailure, match="nicht installiert"):
        stt.ensure()


@pytest.mark.parametrize("kind", ["qwen", "piper"])
def test_tts_uses_independent_interpreter_without_start(monkeypatch, tmp_path, kind):
    selected = independent_paths(monkeypatch, tmp_path)
    worker_dir = tmp_path / "local-tts"
    worker_dir.mkdir()
    name = "qwen_server.py" if kind == "qwen" else "piper_worker.py"
    (worker_dir / name).write_text("# no execution")
    module = local_tts if kind == "qwen" else piper_tts
    monkeypatch.setattr(module, "external_runtime_root", lambda: tmp_path)
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError("synthetic")

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    if kind == "qwen":
        obj = module.LocalTTSProcess()
        monkeypatch.setattr(obj, "_ready", lambda *_: False)
        action = obj.ensure
    else:
        action = module.PiperTTSProcess()._ensure
    if kind=="qwen":
        with pytest.raises(ProviderFailure,match="Qwen-Python-Laufzeit"):
            action()
    else:
        with pytest.raises(FileNotFoundError,match="synthetic"):
            action()
    assert calls == [[str(selected["OMOI_MINI_" + kind.upper() + "_PYTHON"]),
                      str(worker_dir / name)]]
