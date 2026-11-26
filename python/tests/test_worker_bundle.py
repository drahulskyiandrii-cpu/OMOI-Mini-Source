"""OMOI Mini Worker-Source-Bundle: nur synthetische Daten, keine Modelle."""
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from omoi_mini.worker_bundle import (
    FILES, MANIFEST_NAME, WorkerBundleError,
    build_worker_bundle, install_worker_bundle, verify_worker_bundle,
)


@pytest.fixture
def fake_sources(tmp_path: Path):
    source = tmp_path / "fake-checkout"
    source.mkdir()
    for name in FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("TEST-ONLY:" + name).encode("utf-8"))
    # Private Dateien bleiben unberührt, selbst wenn sie direkt daneben liegen.
    (source / "local-tts" / "private.wav").write_bytes(b"SENSITIVE TEST BYTES")
    (source / "local-tts" / "weights.gguf").write_bytes(b"DO NOT PACKAGE")
    (source / ".env").write_text("FAKE_SECRET=never-packaged")
    return source


def test_build_contains_only_six_allowlisted_sources(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    original = (fake_sources / "local-tts" / "private.wav").read_bytes()
    result = build_worker_bundle(fake_sources, target)
    assert result == {"schema": 1, "file_count": 6, "verified": True}
    assert verify_worker_bundle(target) == result
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == set(FILES) | {MANIFEST_NAME}
        manifest = json.loads(archive.read(MANIFEST_NAME))
        assert manifest["schema"] == 1
        assert all(len(x["sha256"]) == 64 for x in manifest["files"])
        assert all(x["name"] in FILES for x in manifest["files"])
    assert (fake_sources / "local-tts" / "private.wav").read_bytes() == original
    assert b"FAKE_SECRET" not in target.read_bytes()
    assert b"DO NOT PACKAGE" not in target.read_bytes()


def test_bundle_is_reproducible(fake_sources, tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    build_worker_bundle(fake_sources, first)
    build_worker_bundle(fake_sources, second)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_dry_run_does_not_create_install_path(fake_sources, tmp_path):
    source_archive = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, source_archive)
    target = tmp_path / "future-install"
    assert install_worker_bundle(source_archive, target)["installed"] is False
    assert not target.exists()
    assert not list(tmp_path.glob("omoi-mini-workers-*"))


def test_apply_extracts_only_sources_to_new_target(fake_sources, tmp_path):
    source_archive = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, source_archive)
    target = tmp_path / "new-workers"
    result = install_worker_bundle(source_archive, target, apply=True)
    assert result["installed"] is True
    actual = {str(f.relative_to(target)).replace("\\", "/")
              for f in target.rglob("*") if f.is_file()}
    assert actual == set(FILES)
    for file in FILES:
        assert (target / file).read_bytes() == (fake_sources / file).read_bytes()
    with pytest.raises(WorkerBundleError, match="existiert bereits"):
        install_worker_bundle(source_archive, target, apply=True)


def test_build_refuses_overwrite_and_symlink_source(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, target)
    with pytest.raises(WorkerBundleError, match="existiert bereits"):
        build_worker_bundle(fake_sources, target)
    (fake_sources / FILES[0]).unlink()
    with pytest.raises(WorkerBundleError, match="Fehlende"):
        build_worker_bundle(fake_sources, tmp_path / "other.zip")


def test_rejects_tampered_file_even_with_valid_zip(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, target)
    with zipfile.ZipFile(target, "a") as archive:
        # Creates duplicate filename: should fail *before* extraction.
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(FILES[0], b"CHANGED")
    with pytest.raises(WorkerBundleError, match="doppelte"):
        verify_worker_bundle(target)


def test_rejects_extra_or_traversal_entry(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, target)
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr("../outside.txt", b"malicious")
    with pytest.raises(WorkerBundleError, match="Unerwartete"):
        install_worker_bundle(target, tmp_path / "never", apply=True)
    assert not (tmp_path / "never").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_rejects_fake_manifest_sha256(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, target)
    rewrite = tmp_path / "tampered.zip"
    with zipfile.ZipFile(target) as original, zipfile.ZipFile(rewrite, "w") as patched:
        for name in original.namelist():
            payload = original.read(name)
            if name == MANIFEST_NAME:
                data = json.loads(payload)
                data["files"][0]["sha256"] = "0" * 64
                payload = json.dumps(data).encode("utf-8")
            patched.writestr(name, payload)
    with pytest.raises(WorkerBundleError, match="Hash"):
        verify_worker_bundle(rewrite)


def test_rejects_archive_containing_symlink(fake_sources, tmp_path):
    target = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, target)
    rewrite = tmp_path / "link.zip"
    with zipfile.ZipFile(target) as original, zipfile.ZipFile(rewrite, "w") as patched:
        for info in original.infolist():
            payload = original.read(info.filename)
            if info.filename == FILES[0]:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            patched.writestr(info, payload)
    with pytest.raises(WorkerBundleError, match="ZIP-Eintrag"):
        verify_worker_bundle(rewrite)


def test_rejects_bad_target_parent(fake_sources, tmp_path):
    bundle = tmp_path / "sources.zip"
    build_worker_bundle(fake_sources, bundle)
    with pytest.raises(WorkerBundleError, match="übergeordneter|Übergeordneter"):
        install_worker_bundle(bundle, tmp_path / "missing" / "workers", apply=False)
    assert not (tmp_path / "missing").exists()


def test_atomic_bundle_build_does_not_clobber_new_target(fake_sources, tmp_path, monkeypatch):
    from omoi_mini import worker_bundle
    target = tmp_path / "race.zip"

    def race(_staged, final):
        Path(final).write_bytes(b"OTHER_USER_DATA")
        raise FileExistsError("target appeared during build")

    monkeypatch.setattr(worker_bundle.os, "link", race)
    with pytest.raises(FileExistsError):
        build_worker_bundle(fake_sources, target)
    assert target.read_bytes() == b"OTHER_USER_DATA"


def test_bundle_blocks_symlink_source_directory(fake_sources, tmp_path):
    root = fake_sources
    actual = root / "local-stt"
    relocated = tmp_path / "external-worker"
    actual.rename(relocated)
    try:
        actual.symlink_to(relocated, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Dieser Windows-Runner erlaubt keine Symlink-Erstellung")
    with pytest.raises(WorkerBundleError, match="Verlinktes"):
        build_worker_bundle(root, tmp_path / "should-not-exist.zip")
    assert not (tmp_path / "should-not-exist.zip").exists()


def test_install_uses_same_verified_snapshot_even_if_zip_is_replaced(
        fake_sources, tmp_path, monkeypatch):
    from omoi_mini import worker_bundle
    archive = tmp_path / "source.zip"
    build_worker_bundle(fake_sources, archive)
    target = tmp_path / "new-install"
    original = worker_bundle._verified_payloads

    def replace_after_verification(path):
        payloads = original(path)
        path.write_bytes(b"REPLACED AFTER VERIFICATION")
        return payloads

    monkeypatch.setattr(worker_bundle, "_verified_payloads", replace_after_verification)
    assert install_worker_bundle(archive, target, apply=True)["installed"]
    for name in FILES:
        assert (target / name).read_bytes() == (fake_sources / name).read_bytes()


def test_install_does_not_overwrite_concurrently_created_directory(
        fake_sources, tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    build_worker_bundle(fake_sources, archive)
    target = tmp_path / "new-install"
    original = Path.mkdir

    def concurrent_target(path, *args, **kwargs):
        if path == target:
            original(path)
            (path / "unrelated.txt").write_bytes(b"keep me")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", concurrent_target)
    with pytest.raises(FileExistsError):
        install_worker_bundle(archive, target, apply=True)
    assert list(target.iterdir()) == [target / "unrelated.txt"]
    assert (target / "unrelated.txt").read_bytes() == b"keep me"


def test_install_cleans_up_its_files_on_copy_error(fake_sources, tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    build_worker_bundle(fake_sources, archive)
    target = tmp_path / "new-install"
    original = Path.open

    def fail_second_write(path, *args, **kwargs):
        if path == target / FILES[1] and args and args[0] == "xb":
            raise OSError("synthetic disk error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second_write)
    with pytest.raises(OSError, match="synthetic disk error"):
        install_worker_bundle(archive, target, apply=True)
    assert not target.exists()
