"""Synthetischer Windows-OneDir-Audit ohne EXE-Start/Modelle/Cloud."""
from pathlib import Path

import pytest

from omoi_mini.portable_audit import PortableAuditError, inspect_portable_folder


@pytest.fixture
def fake_dist(tmp_path: Path):
    root = tmp_path / "portable"
    web = root / "_internal" / "web" / "dist"
    web.mkdir(parents=True)
    (root / "OMOI-Mini.exe").write_bytes(b"MZ" + b"synthetic non-executable")
    (web / "index.html").write_text("<html>Test only</html>", encoding="utf-8")
    (root / "_internal" / "Qt6WebEngineCore.dll").write_bytes(b"synthetic")
    (root / "_internal" / "certifi").mkdir()
    (root / "_internal" / "certifi" / "cacert.pem").write_text("Public CA placeholders")
    return root


def test_clean_package_static_audit_passes(fake_dist):
    result = inspect_portable_folder(fake_dist)
    assert result == {
        "file_count": 4,
        "total_bytes": sum(p.stat().st_size for p in fake_dist.rglob("*") if p.is_file()),
        "animation_count": 0,
        "static_audit_passed": True,
    }


@pytest.mark.parametrize("name", [
    "private-voice.wav",
    "model.gguf",
    "checkpoint.safetensors",
    ".env",
    "notes.sqlite3",
    "avatar.vrm",
    "stolen.token",
])
def test_private_or_model_file_blocks_release(fake_dist, name):
    (fake_dist / "_internal" / name).write_bytes(b"synthetic only")
    with pytest.raises(PortableAuditError, match="Persönliche"):
        inspect_portable_folder(fake_dist)


def test_unknown_animation_blocks_release(fake_dist):
    (fake_dist / "_internal" / "random.vrma").write_bytes(b"not valid")
    with pytest.raises(PortableAuditError, match="VRMA"):
        inspect_portable_folder(fake_dist)


@pytest.mark.parametrize("subdir", ["recordings", "tts-eval", "aicompanion", "credentials"])
def test_private_directory_blocks_release(fake_dist, subdir):
    folder = fake_dist / "_internal" / subdir
    folder.mkdir()
    (folder / "data.txt").write_text("test")
    with pytest.raises(PortableAuditError, match="Privates"):
        inspect_portable_folder(fake_dist)


def test_missing_exe_or_web_blocks_release(fake_dist):
    (fake_dist / "OMOI-Mini.exe").unlink()
    with pytest.raises(PortableAuditError, match="EXE"):
        inspect_portable_folder(fake_dist)
    (fake_dist / "OMOI-Mini.exe").write_bytes(b"MZ")
    (fake_dist / "_internal" / "web" / "dist" / "index.html").unlink()
    with pytest.raises(PortableAuditError, match="Weboberfläche"):
        inspect_portable_folder(fake_dist)


def test_valid_nine_default_vrma_are_allowed_with_manifest(fake_dist):
    """Synthetische glTF-Container, keine echten Avatar-/Animationsdateien."""
    import hashlib
    import json
    import struct
    from omoi_mini.core import BASE_ANIMATION_SLOTS as KNOWN_SLOTS

    approved = fake_dist / "_internal" / "assets" / "default-animations"
    approved.mkdir(parents=True)
    doc = {"asset": {"version": "2.0"},
           "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0"}},
           "animations": [{"channels": [{"sampler": 0}],
                           "samplers": [{"input": 0, "output": 1}]}]}
    payload = json.dumps(doc).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    glb = (struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
           + struct.pack("<I4s", len(payload), b"JSON") + payload)
    entries = []
    for role in KNOWN_SLOTS:
        name = role + ".vrma"
        (approved / name).write_bytes(glb)
        entries.append({"name": name, "license": "CC-BY-4.0",
                        "source_url": "https://example.org/synthetic/" + role,
                        "author": "Testautor", "redistribution_allowed": True,
                        "sha256": hashlib.sha256(glb).hexdigest()})
    (approved / "LICENSES.json").write_text(json.dumps({
        "schema": 1, "files": entries
    }), encoding="utf-8")
    result = inspect_portable_folder(fake_dist)
    assert result["animation_count"] == len(KNOWN_SLOTS)
