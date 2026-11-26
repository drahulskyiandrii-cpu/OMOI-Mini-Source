"""OpenVINO-Entkopplung ohne Start von Node, Python-Modellen oder GPU."""
from pathlib import Path
import sys

import pytest

from omoi_mini import local_stt
from omoi_mini.local_stt import LocalWhisper
from omoi_mini.providers import ProviderFailure


def prepared_worker(tmp_path: Path) -> Path:
    worker = tmp_path / "local-stt" / "worker.mjs"
    worker.parent.mkdir(parents=True)
    worker.write_text("// no-op test worker", encoding="utf-8")
    (worker.parent / "node_modules" / "openvino-genai-node").mkdir(parents=True)
    return worker


def test_frozen_without_model_cannot_read_main_omoi_or_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("OMOI_MINI_STT_MODEL", raising=False)
    monkeypatch.setattr(local_stt, "omoi_model_root",
                        lambda: pytest.fail("Haupt-OMOI-Modellpfad ausgelesen"))
    monkeypatch.setattr(local_stt.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("Fremde lokale STT gestartet"))
    stt = LocalWhisper()
    stt.worker = prepared_worker(tmp_path)
    with pytest.raises(ProviderFailure, match="OMOI_MINI_STT_MODEL"):
        stt.ensure()


def test_frozen_missing_explicit_dir_fails_without_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("OMOI_MINI_STT_MODEL", str(tmp_path / "not-installed"))
    monkeypatch.setattr(local_stt.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("Worker started"))
    stt = LocalWhisper()
    stt.worker = prepared_worker(tmp_path)
    with pytest.raises(ProviderFailure, match="nicht vorhanden"):
        stt.ensure()


def test_frozen_explicit_path_passed_to_child_only(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    selected = tmp_path / "independent-mini-model"
    selected.mkdir()
    monkeypatch.setenv("OMOI_MINI_STT_MODEL", str(selected))
    monkeypatch.setattr(local_stt, "omoi_model_root",
                        lambda: pytest.fail("Haupt-OMOI-Modellpfad ausgelesen"))
    calls = []

    def mock_popen(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError("node simulated missing")

    monkeypatch.setattr(local_stt.subprocess, "Popen", mock_popen)
    stt = LocalWhisper()
    stt.worker = prepared_worker(tmp_path)
    with pytest.raises(ProviderFailure, match="Node.js-Pfad"):
        stt.ensure()
    assert len(calls) == 1
    command, options = calls[0]
    assert command[0] == ["node", str(stt.worker)]
    assert options["env"]["OMOI_MINI_STT_MODEL"] == str(selected)


def test_source_checkout_keeps_accepted_demo_without_node_start(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("OMOI_MINI_DEV_LEGACY","1")
    monkeypatch.delenv("OMOI_MINI_STT_MODEL", raising=False)
    monkeypatch.setattr(local_stt, "omoi_model_root", lambda: tmp_path / "legacy")
    calls = []

    def fail_without_launch(*args, **kwargs):
        calls.append(kwargs["env"]["OMOI_MINI_STT_MODEL"])
        raise FileNotFoundError("node not installed test")

    monkeypatch.setattr(local_stt.subprocess, "Popen", fail_without_launch)
    stt = LocalWhisper()
    stt.worker = prepared_worker(tmp_path)
    with pytest.raises(ProviderFailure, match="Node.js-Pfad"):
        stt.ensure()
    assert calls == [str(tmp_path / "legacy" / "local-models"
                         / "openvino-whisper-medium-int8-ov")]


def test_worker_source_no_longer_contains_main_omoi_fallback():
    source = (Path(__file__).resolve().parents[2] / "local-stt" / "worker.mjs"
             ).read_text(encoding="utf-8")
    assert 'process.env.OMOI_MINI_STT_MODEL' in source
    assert '"aicompanion", "local-models"' not in source
