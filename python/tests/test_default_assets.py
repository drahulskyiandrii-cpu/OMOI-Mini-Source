"""Real first-run asset and precedence regression checks, no GPU."""
from pathlib import Path
import json
import shutil
import pytest
from omoi_mini.core import Store, KNOWN_SLOTS, resolve_animation
from omoi_mini.default_avatar import approved_default_avatar, bundled_default_avatar
from omoi_mini.avatar_catalog import installed_avatars
from omoi_mini.public_assets import validate_public_animations
from omoi_mini.web_server import WebAssets

ROOT=Path(__file__).resolve().parents[2]

def test_default_bundle_covers_every_role(monkeypatch,tmp_path):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"profile"))
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT",str(ROOT))
    pack=ROOT/"assets/default-animations"
    manifest=json.loads((pack/"LICENSES.json").read_text(encoding="utf-8"))
    slots={p.stem for p in validate_public_animations(pack)}
    assert slots=={Path(e["name"]).stem for e in manifest["files"]}
    assert len(slots)==27
    assert len({e["sha256"] for e in manifest["files"]})==len(slots)
    store=Store(tmp_path/"db.sqlite3")
    avatar=bundled_default_avatar()
    assert avatar is not None
    for slot in KNOWN_SLOTS:
        result=resolve_animation(slot,str(avatar),store)
        assert (result.stem if result else None)==(slot if slot in slots else None)

def test_default_only_on_empty_profile_and_available_in_catalog(monkeypatch,tmp_path):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"profile"))
    store=Store(tmp_path/"db.sqlite3")
    default=bundled_default_avatar()
    assert default and WebAssets(tmp_path,store).avatar==default
    assert store.get_settings()["avatar"]==""
    assert any(a.path==default for a in installed_avatars())
    custom=tmp_path/"custom.vrm"; shutil.copyfile(default,custom)
    store.set_setting("avatar",str(custom))
    assert WebAssets(tmp_path,store).avatar==custom
    assert store.get_settings()["avatar"]==str(custom)

def test_avatar_tampering_and_extra_files_block_release(tmp_path):
    source=ROOT/"assets/default-avatar"
    folder=tmp_path/"avatar"; shutil.copytree(source,folder)
    assert approved_default_avatar(folder)
    (folder/"other.vrm").write_bytes(b"private")
    with pytest.raises(ValueError): approved_default_avatar(folder)
    (folder/"other.vrm").unlink()
    with (folder/"default.vrm").open("ab") as f: f.write(b"tamper")
    with pytest.raises(ValueError): approved_default_avatar(folder)
