"""Reine CPU-/Dateisystemtests für eine öffentliche, eigenständige Mini-Version."""
from __future__ import annotations

import io
import json
import struct
import wave
from pathlib import Path

import pytest

from omoi_mini.asset_library import (
    InvalidAvatar, import_vrm, installed_mini_avatars,
    save_selected_animation_defaults,
)
from omoi_mini.voice_assets import InvalidVoice, import_voice_wav, validate_voice_wav
from omoi_mini.core import Store, KNOWN_SLOTS, resolve_animation


def glb(doc:dict) -> bytes:
    data=json.dumps(doc).encode("utf-8")
    data+=b" " * (-len(data)%4)
    return struct.pack("<4sII",b"glTF",2,20+len(data))+struct.pack(
        "<I4s",len(data),b"JSON")+data


def model(path:Path,name:str) -> Path:
    doc={"asset":{"version":"2.0"},"extensions":{
        "VRMC_vrm":{"meta":{"name":name}}},
        "extras":{"padding":"x"*256}}
    path.write_bytes(glb(doc))
    return path


def animation(path:Path) -> Path:
    doc={"asset":{"version":"2.0"},
         "extensions":{"VRMC_vrm_animation":{"specVersion":"1.0"}},
         "animations":[{"channels":[{"sampler":0}],"samplers":[{"input":0,"output":1}]}],
         "extras":{"padding":"x"*48}}
    path.write_bytes(glb(doc))
    return path


def voice(path:Path,seconds:int=4) -> Path:
    with wave.open(str(path),"wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0\x01" * (seconds*24000))
    return path


def test_vrm_is_imported_into_own_profile_not_main(monkeypatch,tmp_path):
    root=tmp_path/"omoi-mini-data"
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(root))
    source=model(tmp_path/"my_character.vrm","Owned")
    original=source.read_bytes()
    imported=import_vrm(source)
    assert imported.parent == root/"avatars"
    assert imported != source
    assert imported.read_bytes()==original
    assert source.read_bytes()==original
    assert import_vrm(source)==imported
    assert installed_mini_avatars()==[imported]


def test_import_rejects_non_vrm_and_symlink(monkeypatch,tmp_path):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"data"))
    source=tmp_path/"invalid.vrm"
    source.write_bytes(b"not a model"*40)
    with pytest.raises(InvalidAvatar):
        import_vrm(source)
    wrong=tmp_path/"plain.vrm"
    wrong.write_bytes(glb({"asset":{"version":"2.0"},"extras":{"padding":"x"*250}}))
    with pytest.raises(InvalidAvatar):
        import_vrm(wrong)


def test_private_animation_defaults_use_selected_roles(monkeypatch,tmp_path):
    root=tmp_path/"my-mini"
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(root))
    store=Store(root/"isolated.sqlite3")
    avatar=str(model(tmp_path/"mine.vrm","Mine"))
    animation_one=animation(tmp_path/"my-idle.vrma")
    animation_two=animation(tmp_path/"my-welcome.vrma")
    store.set_animation(avatar,"idle",str(animation_one))
    store.set_animation(avatar,"welcome",str(animation_two))
    before1=animation_one.read_bytes()
    before2=animation_two.read_bytes()
    resolved=save_selected_animation_defaults(store,avatar)
    assert set(resolved)=={"idle","welcome"}
    assert resolved["idle"]==root/"default-animations"/"idle.vrma"
    assert resolved["welcome"]==root/"default-animations"/"welcome.vrma"
    assert resolved["idle"].read_bytes()==before1
    assert animation_one.read_bytes()==before1
    assert animation_two.read_bytes()==before2
    assert resolve_animation("idle","another-avatar",store)==resolved["idle"]
    assert resolve_animation("idle",avatar,store)==animation_one
    assert set(json.loads((root/"default-animations"/"manifest.json").read_text())["roles"]) == {"idle","welcome"}


def test_missing_private_defaults_does_not_depend_on_main_omoi(monkeypatch,tmp_path):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"data"))
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT",str(tmp_path/"mini-runtime"))
    store=Store(tmp_path/"db.sqlite3")
    assert resolve_animation("idle","missing",store) is None


def test_voice_import_only_local_copy_no_model_use(monkeypatch,tmp_path):
    root=tmp_path/"private"
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(root))
    ref=voice(tmp_path/"sample.wav")
    before=ref.read_bytes()
    imported=import_voice_wav(ref)
    assert imported.parent==root/"voices"
    assert imported.name.endswith(".wav")
    assert imported.read_bytes()==before
    assert ref.read_bytes()==before
    assert import_voice_wav(ref)==imported
    assert validate_voice_wav(imported)==4.0


def test_voice_import_rejects_too_short(monkeypatch,tmp_path):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"data"))
    ref=voice(tmp_path/"too_short.wav",seconds=1)
    with pytest.raises(InvalidVoice):
        import_voice_wav(ref)


def test_custom_phrases_persist_and_can_reset(tmp_path):
    db=tmp_path/"db.sqlite3"
    store=Store(db)
    assert "OMOI Mini" in store.phrase("introduce","de")
    store.set_setting("introduce_de","Ich bin eine neu konfigurierte Assistentin.")
    store.set_setting("hello_ru","Добрый день!")
    reloaded=Store(db)
    assert reloaded.phrase("introduce","de")=="Ich bin eine neu konfigurierte Assistentin."
    assert reloaded.phrase("hello","ru")=="Добрый день!"
    reloaded.set_setting("introduce_de","")
    assert "OMOI Mini" in reloaded.phrase("introduce","de")
    with pytest.raises(ValueError):
        reloaded.set_setting("hello_ru","x"*1501)
