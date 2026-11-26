from pathlib import Path
from types import SimpleNamespace
import pytest
from omoi_mini.core import Store, ANIMATION_ROLES, ALTERNATIVE_ANIMATION_SLOTS, resolve_animation
from omoi_mini.gui import Bridge
from omoi_mini.web_server import WebAssets

def test_variant_slots_and_private_avatar_assignments(tmp_path,monkeypatch):
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT",str(tmp_path/"empty-bundle"))
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path))
    store=Store(tmp_path/"db.sqlite")
    assert len(ALTERNATIVE_ANIMATION_SLOTS)==2*len(ANIMATION_ROLES)
    avatar_a=tmp_path/"a.vrm"; avatar_b=tmp_path/"b.vrm"
    primary=tmp_path/"primary.vrma"; primary.write_bytes(b"primary")
    alternative=tmp_path/"alt.vrma"; alternative.write_bytes(b"alt")
    store.set_animation(str(avatar_a),"idle",str(primary))
    store.set_animation(str(avatar_a),"idle_alt1",str(alternative))
    assets=WebAssets(tmp_path,store); assets.set_avatar(avatar_a)
    fake=SimpleNamespace(window=SimpleNamespace(assets=assets,store=store))
    assert Bridge._available_alternatives(fake)==["idle_alt1"]
    assert assets._selected_asset("/api/animation/idle")==primary
    assert assets._selected_asset("/api/animation/idle_alt1")==alternative
    assets.set_avatar(avatar_b)
    assert Bridge._available_alternatives(fake)==[]
    assert assets._selected_asset("/api/animation/idle_alt1") is None
    with pytest.raises(ValueError):
        store.set_animation(str(avatar_a),"idle_alt3",str(alternative))
    store.reset_animation(str(avatar_a),"idle_alt1")
    assert resolve_animation("idle_alt1",str(avatar_a),store) is None
    assert alternative.exists()
    assert resolve_animation("idle",str(avatar_a),store)==primary

def test_private_defaults_available_and_reset_preserves_default(tmp_path,monkeypatch):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path))
    store=Store(tmp_path/"db.sqlite")
    folder=tmp_path/"default-animations"; folder.mkdir()
    default=folder/"idle_alt2.vrma"; default.write_bytes(b"default")
    other=tmp_path/"custom.vrma"; other.write_bytes(b"custom")
    avatar=str(tmp_path/"avatar.vrm")
    assert resolve_animation("idle_alt2",avatar,store)==default
    store.set_animation(avatar,"idle_alt2",str(other))
    assert resolve_animation("idle_alt2",avatar,store)==other
    store.reset_animation(avatar,"idle_alt2")
    assert resolve_animation("idle_alt2",avatar,store)==default


def test_exclusive_local_pack_does_not_mix_bundled_motion(tmp_path,monkeypatch):
    monkeypatch.setenv("OMOI_MINI_DATA_DIR",str(tmp_path/"profile"))
    monkeypatch.setenv("OMOI_MINI_RUNTIME_ROOT",str(tmp_path))
    bundled=tmp_path/"assets/default-animations"
    bundled.mkdir(parents=True)
    (bundled/"idle_alt2.vrma").write_bytes(b"bundled")
    store=Store(tmp_path/"db.sqlite")
    assert resolve_animation("idle_alt2","",store)==bundled/"idle_alt2.vrma"
    monkeypatch.setenv("OMOI_MINI_LOCAL_ANIMATIONS_ONLY","1")
    assert resolve_animation("idle_alt2","",store) is None
    custom=tmp_path/"custom.vrma"
    custom.write_bytes(b"custom")
    store.set_animation("","idle_alt2",str(custom))
    assert resolve_animation("idle_alt2","",store)==custom
