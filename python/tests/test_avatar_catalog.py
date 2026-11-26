"""VRM-Metadaten und sichere Auswahl der bereits lokal verfügbaren Modelle."""
import json
import struct
from pathlib import Path

from omoi_mini.avatar_catalog import _metadata_name, installed_avatars


def fake_vrm(name: str) -> bytes:
    document = {
        "asset": {"version": "2.0"},
        "extensions": {"VRMC_vrm": {"meta": {"name": name}}},
    }
    metadata = json.dumps(document).encode("utf-8")
    metadata += b" " * ((-len(metadata)) % 4)
    length = 20 + len(metadata)
    return struct.pack("<4sII", b"glTF", 2, length) + struct.pack(
        "<I4s", len(metadata), b"JSON"
    ) + metadata


def test_names_from_bounded_vrm_metadata(tmp_path: Path):
    source = tmp_path / "avatar.vrm"
    source.write_bytes(fake_vrm("Momo  Test"))
    assert _metadata_name(source) == "Momo Test"
    source.write_bytes(fake_vrm("1"))
    assert _metadata_name(source) == ""
    source.write_bytes(b"not-a-vrm")
    assert _metadata_name(source) == ""


def test_catalog_only_lists_files_inside_installed_library(monkeypatch, tmp_path: Path):
    root = tmp_path / "mini-profile"
    library = root / "avatars"
    first = library / "first.vrm"
    second = library / "second.vrm"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(fake_vrm("Hana"))
    second.write_bytes(fake_vrm("1"))
    outside = tmp_path / "other.vrm"
    outside.write_bytes(fake_vrm("Hidden"))
    monkeypatch.setenv("OMOI_MINI_DATA_DIR", str(root))
    monkeypatch.setattr("omoi_mini.avatar_catalog.bundled_default_avatar",lambda:None)
    models = installed_avatars()
    assert len(models) == 2
    assert models[0].label == "Hana · #01"
    assert models[1].label == "VRM-Modell 02 · #02"
    assert all(item.path.is_file() for item in models)
    assert all("Hidden" not in item.label for item in models)
