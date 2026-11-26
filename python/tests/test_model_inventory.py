import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from omoi_mini import model_inventory as inv
from omoi_mini import model_installer as core
from omoi_mini.model_releases import release_for


def rows(profile, settings=None, env=None):
    return {r["id"]: r for r in inv.installation_rows(profile, settings or {}, env=env or {})}


def test_space_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(inv.shutil, "disk_usage", lambda p: SimpleNamespace(free=1))
    result = rows(tmp_path)
    assert result["gemma4-e2b-gguf"]["enough_space"] is False
    assert result["gemma4-e2b-gguf"]["managed_status"] == "absent"
    assert result["gemma4-12b-gguf"]["enough_space"] is None
    assert not list(tmp_path.iterdir())
    assert str(tmp_path) not in json.dumps(result)


def test_missing_profile_not_created(tmp_path):
    profile = tmp_path / "missing"
    result = rows(profile)
    assert result["gemma4-e2b-gguf"]["available_space_bytes"] is None
    assert not profile.exists()


def test_gguf_configured_not_certified(tmp_path):
    candidate = tmp_path / "custom.gguf"
    candidate.write_bytes(b"GGUFfake")
    result = rows(tmp_path, {"llm_gemma12_file": str(candidate)})
    assert result["gemma4-12b-gguf"]["existing_status"] == "configured_present_unverified"
    candidate.write_bytes(b"BAD!")
    assert rows(tmp_path, {"llm_gemma12_file": str(candidate)})["gemma4-12b-gguf"]["existing_status"] == "configured_missing_or_invalid"


def test_busy_and_foreign_directory(tmp_path):
    root = tmp_path / inv.ROOT_NAME
    root.mkdir()
    lock = root / inv.LOCK
    lock.write_text("test")
    assert rows(tmp_path)["gemma4-e2b-gguf"]["managed_status"] == "busy"
    lock.unlink()
    target = root / "gemma4-e2b-gguf"
    target.mkdir()
    assert rows(tmp_path)["gemma4-e2b-gguf"]["managed_status"] == "conflict"
    assert target.exists()


def test_managed_marker_and_size_not_hash(tmp_path):
    root = tmp_path / inv.ROOT_NAME
    root.mkdir()
    manifest = core.Manifest("demo", "a"*40, (core.ModelFile("model.bin", 4, "b"*64),))
    target = root / "demo"
    target.mkdir()
    (target / core.MARKER).write_text(core._metadata(manifest), encoding="utf-8")
    (target / "model.bin").write_bytes(b"fake")
    assert inv._managed_state(root, manifest) == "manifest_sizes_match"
    (target / "model.bin").write_bytes(b"x")
    assert inv._managed_state(root, manifest) == "conflict"


def test_existing_stt_candidate(tmp_path, monkeypatch):
    release = release_for("whisper-small-cuda")
    files = tuple(replace(f, size=3) for f in release.manifest.files)
    release = replace(release, manifest=replace(release.manifest, files=files))
    real = inv.release_for
    monkeypatch.setattr(inv, "release_for", lambda mid: release if mid == release.manifest.model_id else real(mid))
    model = tmp_path / "external"
    model.mkdir()
    for f in files:
        if f.path != "README.md":
            (model / f.path).write_bytes(b"abc")
    settings = {"runtime_cuda_model": str(model)}
    assert rows(tmp_path, settings)["whisper-small-cuda"]["existing_status"] == "candidate_sizes_match"
    (model / "model.bin").unlink()
    assert rows(tmp_path, settings)["whisper-small-cuda"]["existing_status"] == "candidate_incomplete_or_different"


def test_hf_snapshot_exact_revision(tmp_path, monkeypatch):
    release = release_for("qwen3-tts-1.7b")
    files = tuple(replace(f, size=3) for f in release.manifest.files)
    release = replace(release, manifest=replace(release.manifest, files=files))
    real = inv.release_for
    monkeypatch.setattr(inv, "release_for", lambda mid: release if mid == release.manifest.model_id else real(mid))
    cache = tmp_path / "hub"
    snapshot = cache / ("models--" + release.repository.replace("/", "--")) / "snapshots" / release.manifest.revision
    for f in files:
        if f.path != "README.md":
            path = snapshot / f.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"abc")
    assert rows(tmp_path, env={"HF_HUB_CACHE": str(cache)})["qwen3-tts-1.7b"]["existing_status"] == "candidate_sizes_match"
    snapshot.rename(snapshot.with_name("other-revision"))
    assert rows(tmp_path, env={"HF_HUB_CACHE": str(cache)})["qwen3-tts-1.7b"]["existing_status"] == "not_found"
