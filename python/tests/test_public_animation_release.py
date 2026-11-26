"""Synthetische VRMA/Manifeste: keine privaten Assets, kein Modellstart."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from omoi_mini.core import BASE_ANIMATION_SLOTS as KNOWN_SLOTS
from omoi_mini.public_assets import PublicAssetError, validate_public_animations


def sample_vrma() -> bytes:
    payload = json.dumps({
        "asset": {"version": "2.0"},
        "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0"}},
        "animations": [{"channels": [{"sampler": 0}],
                        "samplers": [{"input": 0, "output": 1}]}],
    }).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
            + struct.pack("<I4s", len(payload), b"JSON") + payload)


def write_manifest(folder: Path, entries: list[dict]) -> None:
    (folder / "LICENSES.json").write_text(
        json.dumps({"schema": 1, "files": entries}), encoding="utf-8"
    )


@pytest.fixture
def approved_bundle(tmp_path: Path):
    folder = tmp_path / "assets"
    folder.mkdir()
    entries = []
    for slot in KNOWN_SLOTS:
        name = f"{slot}.vrma"
        content = sample_vrma()
        (folder / name).write_bytes(content)
        entries.append({
            "name": name,
            "license": "CC-BY-4.0",
            "source_url": "https://example.org/approved/" + slot,
            "author": "Synthetischer Testautor",
            "sha256": hashlib.sha256(content).hexdigest(),
            "redistribution_allowed": True,
        })
    write_manifest(folder, entries)
    return folder, entries


def test_no_public_assets_still_buildable(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    assert validate_public_animations(folder) == []


def test_nine_explicitly_approved_roles_accepted(approved_bundle):
    folder, entries = approved_bundle
    names = {path.name for path in validate_public_animations(folder)}
    assert names == {f"{slot}.vrma" for slot in KNOWN_SLOTS}
    assert len(entries) == 9


def test_partial_pack_is_blocked(approved_bundle):
    folder, _ = approved_bundle
    (folder / "idle.vrma").unlink()
    with pytest.raises(PublicAssetError, match="Pflichtrollen"):
        validate_public_animations(folder)


def test_missing_or_stale_manifest_is_blocked(approved_bundle):
    folder, _ = approved_bundle
    (folder / "LICENSES.json").unlink()
    with pytest.raises(PublicAssetError, match="LICENSES.json"):
        validate_public_animations(folder)
    for file in folder.glob("*.vrma"):
        file.unlink()
    write_manifest(folder, [])
    with pytest.raises(PublicAssetError, match="Verwaistes"):
        validate_public_animations(folder)


def test_changed_asset_is_blocked_even_with_valid_metadata(approved_bundle):
    folder, _ = approved_bundle
    clip = folder / "idle.vrma"
    clip.write_bytes(clip.read_bytes() + b"tamper")
    with pytest.raises(PublicAssetError):
        validate_public_animations(folder)


@pytest.mark.parametrize("field,value,problem", [
    ("redistribution_allowed", False, "Weitergabe"),
    ("redistribution_allowed", "yes", "Weitergabe"),
    ("author", "", "Urheber"),
    ("license", "unknown", "Lizenz"),
    ("source_url", "http://example.org", "HTTPS"),
    ("source_url", "https://u:p@example.org", "HTTPS"),
    ("sha256", "0" * 63, "SHA-256"),
])
def test_incomplete_release_permission_blocked(approved_bundle, field, value, problem):
    folder, entries = approved_bundle
    entries[0][field] = value
    write_manifest(folder, entries)
    with pytest.raises(PublicAssetError, match=problem):
        validate_public_animations(folder)


def test_duplicate_role_in_manifest_blocked(approved_bundle):
    folder, entries = approved_bundle
    entries[1]["name"] = entries[0]["name"]
    write_manifest(folder, entries)
    with pytest.raises(PublicAssetError, match="doppelte"):
        validate_public_animations(folder)


def test_invalid_vrma_rejected_even_if_hash_matches(approved_bundle):
    folder, entries = approved_bundle
    damaged = b"invalid-vrma-file"
    (folder / entries[0]["name"]).write_bytes(damaged)
    entries[0]["sha256"] = hashlib.sha256(damaged).hexdigest()
    write_manifest(folder, entries)
    with pytest.raises(PublicAssetError, match="VRMA"):
        validate_public_animations(folder)


def test_private_or_unlisted_asset_never_enters_bundle(approved_bundle):
    folder, entries = approved_bundle
    accidental = folder / "accidental-private-voice.wav"
    accidental.write_bytes(b"not distributable")
    with pytest.raises(PublicAssetError, match="Unbekannte"):
        validate_public_animations(folder)
    accidental.unlink()
    entries[0]["extra_secret_field"] = "do not export"
    write_manifest(folder, entries)
    with pytest.raises(PublicAssetError, match="unerwartete"):
        validate_public_animations(folder)
    del entries[0]["extra_secret_field"]
    manifest = {"schema": 1, "files": entries, "unexpected_private_data": "blocked"}
    (folder / "LICENSES.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PublicAssetError, match="Schema"):
        validate_public_animations(folder)


def test_source_url_with_query_token_or_malformed_host_blocked(approved_bundle):
    folder, entries = approved_bundle
    for address in ("https://example.org/file?token=private", "https://[invalid"):
        entries[0]["source_url"] = address
        write_manifest(folder, entries)
        with pytest.raises(PublicAssetError, match="HTTPS"):
            validate_public_animations(folder)


def test_apache_motion_requires_notice_and_license(approved_bundle):
    folder, entries = approved_bundle
    entries[0]["license"] = "Apache-2.0"
    write_manifest(folder, entries)
    with pytest.raises(PublicAssetError, match="NOTICE"):
        validate_public_animations(folder)
    (folder / "NOTICE.md").write_text("Test notice", encoding="utf-8")
    (folder / "LICENSE-Apache-2.0.txt").write_text("Test license", encoding="utf-8")
    assert len(validate_public_animations(folder)) == len(entries)
