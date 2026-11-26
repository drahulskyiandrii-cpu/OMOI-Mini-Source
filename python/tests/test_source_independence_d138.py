"""Source und EXE brauchen keine Hauptprojekt-Installation."""
import sys
import pytest
from omoi_mini import providers,local_stt
from omoi_mini.core import legacy_development_enabled


def test_source_default_does_not_access_main_project(monkeypatch,tmp_path):
    monkeypatch.delenv("OMOI_MINI_DEV_LEGACY",raising=False)
    monkeypatch.delenv("CARTESIA_VOICE_ID",raising=False)
    monkeypatch.delenv("OMOI_MINI_STT_MODEL",raising=False)
    monkeypatch.setattr(providers,"omoi_model_root",lambda:pytest.fail("legacy path"))
    monkeypatch.setattr(local_stt,"omoi_model_root",lambda:pytest.fail("legacy path"))
    with pytest.raises(providers.ProviderFailure,match="eigenes GGUF"):
        providers.LocalGemma().ensure()
    with pytest.raises(providers.ProviderFailure,match="CARTESIA_VOICE_ID"):
        providers.nika_voice_id()
    worker=local_stt.LocalWhisper()
    worker.worker=tmp_path/"worker.mjs"
    worker.worker.write_text("// synthetic")
    with pytest.raises(providers.ProviderFailure,match="OMOI_MINI_STT_MODEL"):
        worker.ensure()


def test_frozen_ignores_explicit_legacy_opt_in(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_DEV_LEGACY","1")
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    assert not legacy_development_enabled()
