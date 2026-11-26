"""Portable-Entkopplung: ausschließlich Fake-Prozesse, keine LLM-/TTS-Inferenz."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from omoi_mini import providers
from omoi_mini.providers import LocalGemma, ProviderFailure, nika_voice_id


def test_portable_never_discovers_main_omoi_llm(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(providers, "omoi_model_root",
                        lambda: pytest.fail("Haupt-OMOI-Pfad gelesen"))
    monkeypatch.setattr(providers.subprocess, "Popen",
                        lambda *a, **kw: pytest.fail("Modellprozess gestartet"))
    engine = LocalGemma()
    # Selbst ein fremder, bereits laufender localhost-Dienst darf nicht
    # als stillschweigende Portable-Konfiguration akzeptiert werden.
    monkeypatch.setattr(engine, "_available",
                        lambda: pytest.fail("Fremden LLM-Endpunkt geprüft"))
    with pytest.raises(ProviderFailure, match="eigenes GGUF"):
        engine.ensure()


def test_portable_requires_own_cartesia_voice_id(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("CARTESIA_VOICE_ID", raising=False)
    monkeypatch.setattr(providers, "omoi_model_root",
                        lambda: pytest.fail("Privates Haupt-OMOI-Profil gelesen"))
    with pytest.raises(ProviderFailure, match="CARTESIA_VOICE_ID"):
        nika_voice_id()


def test_portable_explicit_voice_id_works_without_main_omoi(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("CARTESIA_VOICE_ID", "synthetic-test-voice")
    monkeypatch.setattr(providers, "omoi_model_root",
                        lambda: pytest.fail("Privates Haupt-OMOI-Profil gelesen"))
    assert nika_voice_id() == "synthetic-test-voice"


def test_source_checkout_preserves_accepted_voice_profile(monkeypatch, tmp_path: Path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("OMOI_MINI_DEV_LEGACY","1")
    monkeypatch.delenv("CARTESIA_VOICE_ID", raising=False)
    profiles = tmp_path / "voices"
    profiles.mkdir()
    (profiles / "profiles.json").write_text(json.dumps({
        "profiles": [{
            "name": "Nika",
            "bindings": [{"providerId": "cartesia", "providerVoiceId": "test-only"}],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(providers, "omoi_model_root", lambda: tmp_path)
    assert nika_voice_id() == "test-only"


def test_portable_explicit_local_model_uses_only_selected_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(providers, "omoi_model_root",
                        lambda: pytest.fail("Haupt-OMOI-Pfad gelesen"))
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "my-model.gguf"
    runtime.write_bytes(b"FAKE")  # Never execute
    model.write_bytes(b"FAKE")
    captured = []

    def fake_popen(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda **kw: 0)

    monkeypatch.setattr(providers.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(providers.httpx, "get",
                        lambda *a, **kw: SimpleNamespace(status_code=200))
    engine = LocalGemma(str(model), str(runtime))
    checks = iter([False, False])
    monkeypatch.setattr(engine, "_available", lambda: next(checks, False))
    engine.ensure()
    assert len(captured) == 1
    assert captured[0][0] == str(runtime)
    assert captured[0][captured[0].index("-m")+1] == str(model)
    engine.close()
