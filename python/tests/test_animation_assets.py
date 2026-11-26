"""VRMA-Importtests mit künstlichen, frei erzeugten GLB-Dateien."""
import json
import struct
from pathlib import Path

import pytest

from omoi_mini.animation_assets import InvalidAnimation, import_vrma, validate_vrma


def sample_vrma(*, extension=True, channels=True) -> bytes:
    document = {
        "asset": {"version": "2.0"},
        "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0"}} if extension else {},
        "animations": [{
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}]
            if channels else [],
            "samplers": [{"input": 0, "output": 1}],
        }],
    }
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * ((-len(text)) % 4)
    size = 20 + len(text)
    return struct.pack("<4sII", b"glTF", 2, size) + struct.pack("<I4s", len(text), b"JSON") + text


def test_valid_vrma_archives_idempotently(tmp_path: Path):
    source = tmp_path / "probe.vrma"
    binary = sample_vrma()
    source.write_bytes(binary)
    assert validate_vrma(source) == (1, 1)
    destination = tmp_path / "user-data" / "animations"
    first = import_vrma(source, destination)
    second = import_vrma(source, destination)
    assert first.path == second.path
    assert first.path.read_bytes() == binary
    assert first.path.parent == destination
    assert first.original_name == "probe.vrma"
    assert first.channel_count == 1
    assert source.read_bytes() == binary
    assert len(list(destination.glob("*.vrma"))) == 1


@pytest.mark.parametrize("content", [
    b"glTF", sample_vrma(extension=False), sample_vrma(channels=False),
    sample_vrma()[:8], b"invalid",
])
def test_invalid_vrma_rejected(tmp_path: Path, content: bytes):
    source = tmp_path / "kaputt.vrma"
    source.write_bytes(content)
    with pytest.raises(InvalidAnimation):
        validate_vrma(source)


def test_false_extension_rejected(tmp_path: Path):
    source = tmp_path / "bewegung.vmd"
    source.write_bytes(sample_vrma())
    with pytest.raises(InvalidAnimation):
        import_vrma(source, tmp_path / "store")


def test_wrong_glb_declared_size_rejected(tmp_path: Path):
    source = tmp_path / "kaputt.vrma"
    binary = bytearray(sample_vrma())
    struct.pack_into("<I", binary, 8, len(binary) + 4)
    source.write_bytes(binary)
    with pytest.raises(InvalidAnimation):
        validate_vrma(source)


def test_reject_oversized_without_reading_contents(tmp_path: Path):
    source = tmp_path / "gross.vrma"
    with source.open("wb") as file:
        file.truncate(128 * 1024 * 1024 + 1)
    with pytest.raises(InvalidAnimation):
        validate_vrma(source)


def test_preview_does_not_change_assignment_before_confirmation(tmp_path: Path):
    from omoi_mini.core import Store
    from omoi_mini.web_server import WebAssets

    source = tmp_path / "probe.vrma"
    source.write_bytes(sample_vrma())
    info = import_vrma(source, tmp_path / "private-animations")
    store = Store(tmp_path / "store.sqlite3")
    dist = tmp_path / "dist"
    dist.mkdir()
    server = WebAssets(dist, store)
    server.staged_animations["welcome"] = info.path
    assert server._selected_asset("/api/preview/welcome") == info.path
    assert server._selected_asset("/api/animation/welcome") != info.path
    assert store.animations("avatar.vrm") == {}

    store.set_animation("avatar.vrm", "welcome", str(info.path))
    assert store.animations("avatar.vrm") == {"welcome": str(info.path)}
    store.set_animation("another.vrm", "welcome", str(info.path))
    store.reset_animation("avatar.vrm", "welcome")
    assert store.animations("avatar.vrm") == {}
    assert store.animations("another.vrm") == {"welcome": str(info.path)}
    with pytest.raises(ValueError):
        store.reset_animation("avatar.vrm", "not-a-role")
