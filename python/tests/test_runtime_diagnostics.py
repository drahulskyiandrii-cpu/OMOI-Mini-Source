"""Portable-Worker-Diagnose: nur Datei-Metadaten, keine KI-Initialisierung."""
from pathlib import Path

from omoi_mini.runtime_diagnostics import inspect_local_runtimes


def test_empty_install_is_read_only(tmp_path: Path):
    root = tmp_path / "not-installed"
    data = tmp_path / "not-created"
    report = inspect_local_runtimes(root, data, env={}, node_found=False)
    assert not root.exists()
    assert not data.exists()
    assert set(report) == {"openvino_stt", "cuda_stt", "qwen_tts", "piper_tts"}
    assert all(value is False for service in report.values() for value in service.values())


def test_explicit_openvino_model_reports_files_only(tmp_path: Path):
    root = tmp_path / "workers"
    stt = root / "local-stt"
    stt.mkdir(parents=True)
    (stt / "worker.mjs").write_text("// fake")
    (stt / "node_modules" / "openvino-genai-node").mkdir(parents=True)
    model = tmp_path / "my-openvino"
    model.mkdir()
    report = inspect_local_runtimes(root, tmp_path / "userdata",
                                    env={"OMOI_MINI_STT_MODEL": str(model)},
                                    node_found=True)
    assert all(report["openvino_stt"].values())
    assert not report["qwen_tts"]["server_file"]


def test_cuda_and_qwen_require_separate_files_not_model_loading(tmp_path: Path):
    root = tmp_path / "workers"
    user = tmp_path / "isolated"
    (root / "local-stt").mkdir(parents=True)
    (root / "local-stt" / "cuda_worker.py").write_text("# fake")
    (root / "local-tts").mkdir()
    (root / "local-tts" / "qwen_server.py").write_text("# fake")
    (root / "local-tts" / "piper_worker.py").write_text("# fake")
    python = user / "tts-eval" / "qwen-venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"FAKE_NO_EXECUTION")
    overlay = user / "stt-eval" / "faster-whisper-overlay"
    overlay.mkdir(parents=True)
    model_file = user / "stt-eval" / "faster-whisper-small-ct2" / "model.bin"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"FAKE_NO_MODEL")
    piper = user / "piper-custom.exe"
    piper.write_bytes(b"FAKE")
    report = inspect_local_runtimes(root, user,
                                    env={"OMOI_MINI_PIPER_PYTHON": str(piper)},
                                    node_found=False)
    assert all(report["cuda_stt"].values())
    assert all(report["qwen_tts"].values())
    assert all(report["piper_tts"].values())


def test_private_paths_are_not_returned(tmp_path: Path):
    secret = tmp_path / "private-model-and-name"
    secret.mkdir()
    result = inspect_local_runtimes(tmp_path, tmp_path,
                                    env={"OMOI_MINI_STT_MODEL": str(secret)},
                                    node_found=False)
    assert str(secret) not in str(result)
    assert all(isinstance(v, bool) for s in result.values() for v in s.values())
