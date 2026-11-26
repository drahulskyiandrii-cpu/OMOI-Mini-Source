"""Mini-Demoliste speichert nur Pfadverweise, nie gemeinsame OMOI-VRM-Dateien."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from omoi_mini.core import Store, resolve_avatar


def model(path: Path, name: str) -> Path:
    doc = {
        "asset": {"version": "2.0"},
        "extensions": {"VRMC_vrm": {"meta": {"name": name}}},
        "extras": {"padding": "x" * 128},
    }
    data=json.dumps(doc).encode("utf-8")
    data += b" " * (-len(data) % 4)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 20+len(data))
        +struct.pack("<I4s",len(data),b"JSON")+data
    )
    return path


def test_random_first_avatar_is_not_automatically_selected(tmp_path: Path,monkeypatch):
    root=tmp_path/"aicompanion"
    first=model(root/"live2d-models"/"000"/"avatar.vrm","Not for demo")
    second=model(root/"live2d-models"/"001"/"avatar.vrm","Approved")
    monkeypatch.setattr("omoi_mini.core.omoi_model_root",lambda:root)
    assert resolve_avatar("",[]) is None
    assert resolve_avatar(str(first),[])==first
    assert resolve_avatar(str(first),[str(second)])==second
    assert resolve_avatar(str(second),[str(second)])==second
    assert resolve_avatar(str(first),[str(tmp_path/"missing.vrm")])==first


def test_favorites_persist_without_copying_files(tmp_path: Path):
    db=tmp_path/"test.sqlite3"
    library=tmp_path/"main-omoi"
    unwanted=model(library/"unwanted"/"avatar.vrm","Not approved")
    candidate=model(library/"candidate"/"avatar.vrm","Good for demo")
    old_source=unwanted.read_bytes()
    good_source=candidate.read_bytes()
    s=Store(db)
    assert s.favorite_avatars()==[]
    s.set_avatar_favorite(str(candidate),True)
    s.set_avatar_favorite(str(candidate),True)
    assert Store(db).favorite_avatars()==[str(candidate.resolve())]
    assert resolve_avatar("",Store(db).favorite_avatars())==candidate.resolve()
    assert good_source==candidate.read_bytes()
    assert old_source==unwanted.read_bytes()
    assert sorted(path.name for path in library.iterdir())==["candidate","unwanted"]
    s.set_avatar_favorite(str(candidate),False)
    assert Store(db).favorite_avatars()==[]
    assert candidate.read_bytes()==good_source


def test_only_explicit_valid_vrm_can_be_favorited(tmp_path: Path):
    s=Store(tmp_path/"test.sqlite3")
    missing=tmp_path/"nonexisting.vrm"
    with pytest.raises(ValueError):
        s.set_avatar_favorite(str(missing),True)
    wrong=tmp_path/"asset.wav"
    wrong.write_bytes(b"123" * 50)
    with pytest.raises(ValueError):
        s.set_avatar_favorite(str(wrong),True)
    valid=model(tmp_path/"valid.vrm","Valid")
    with pytest.raises(ValueError):
        s.set_avatar_favorite(str(valid),"yes")
    assert s.favorite_avatars()==[]


def test_two_favorite_models_keep_order_and_preferred(tmp_path: Path):
    db=Store(tmp_path/"avatar.sqlite3")
    first=model(tmp_path/"first.vrm","First")
    second=model(tmp_path/"second.vrm","Second")
    db.set_avatar_favorite(str(first),True)
    db.set_avatar_favorite(str(second),True)
    order=Store(tmp_path/"avatar.sqlite3").favorite_avatars()
    assert order==[str(first),str(second)]
    assert resolve_avatar("",order)==first
    assert resolve_avatar(str(second),order)==second
    db.set_avatar_favorite(str(first),False)
    assert resolve_avatar(str(first),db.favorite_avatars())==second
