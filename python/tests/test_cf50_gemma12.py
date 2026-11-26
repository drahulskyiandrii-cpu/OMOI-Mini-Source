"""CF50/12B integration gates without importing or starting a model."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omoi_mini import gui, local_tts
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.providers import LocalGemma, ProviderFailure


def test_new_profiles_default_to_original_and_persist(tmp_path):
    store=Store(tmp_path/"db")
    assert store.get_settings()["qwen_variant"]=="base"
    assert store.get_settings()["llm_local_profile"]=="standard"
    store.set_setting("qwen_variant","cf050")
    store.set_setting("llm_local_profile","gemma12")
    assert Store(store.path).get_settings()["qwen_variant"]=="cf050"
    with pytest.raises(ValueError):
        store.set_setting("qwen_variant","cf100")


def test_cf50_distinct_model_id_and_native_speed(monkeypatch):
    monkeypatch.setenv("OMOI_MINI_QWEN_SIZE","0.6B")
    assert local_tts.local_tts_model_id("cf050")=="qwen3-tts-1.7b-base-cf050"
    assert local_tts.local_tts_default_speed("ru","cf050")==1.0
    assert local_tts.local_tts_model_id()=="qwen3-tts-0.6b-base"


def test_cf50_rejects_original_service_on_same_port(monkeypatch):
    real=httpx.Client
    transport=httpx.MockTransport(lambda req:httpx.Response(200,json={
        "service":"omoi-mini-local-tts","model":"qwen3-tts-1.7b-base"}))
    monkeypatch.setattr(httpx,"Client",lambda **kw:real(transport=transport,**kw))
    with pytest.raises(ProviderFailure,match="anderen Modell"):
        local_tts.LocalTTSProcess(variant="cf050")._ready("http://127.0.0.1:18190")


def test_cf50_wav_rejects_wrong_response_variant(monkeypatch):
    real=httpx.AsyncClient
    payloads=[]
    def respond(req):
        payloads.append(json.loads(req.content))
        return httpx.Response(200,content=b"RIFFxxxxWAVE"+b"x"*50)
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(respond),**kw))
    with pytest.raises(ProviderFailure,match="Modellvariante"):
        local_tts.local_tts_wav("test","ru",variant="cf050")
    assert payloads[0]["model"]=="qwen3-tts-1.7b-base-cf050"
    assert payloads[0]["speed"]==1.0


def test_controller_12b_and_cf50_snapshot_cache_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    store=Store(tmp_path/"db")
    store.set_setting("llm_model_file","standard.gguf")
    store.set_setting("llm_server_file","standard.exe")
    base=Controller(store)
    try:
        base_cache=base._cache_path("text","ru","local")
    finally:
        base.close()
    store.set_setting("qwen_variant","cf050")
    store.set_setting("llm_local_profile","gemma12")
    store.set_setting("llm_gemma12_file","alternative.gguf")
    store.set_setting("llm_gemma12_server_file","alternative.exe")
    ctrl=Controller(store)
    try:
        assert ctrl.gemma.model_file=="alternative.gguf"
        assert ctrl.gemma.server_file=="alternative.exe"
        assert ctrl.gemma.strict_owned
        assert ctrl.local_voice.variant=="cf050"
        assert ctrl._cache_path("text","ru","local")!=base_cache
        store.set_setting("qwen_variant","base")
        assert ctrl.local_voice.variant=="cf050"
        assert ctrl._cache_path("text","ru","local")!=base_cache
    finally:
        ctrl.close()


def test_12b_never_reuses_foreign_healthy_endpoint(monkeypatch):
    llm=LocalGemma("12b.gguf","llama-server.exe",strict_owned=True)
    monkeypatch.setattr(llm,"_available",lambda:True)
    with pytest.raises(ProviderFailure,match="Port belegt"):
        llm.ensure()


def test_12b_picker_persists_separately(tmp_path, monkeypatch):
    store=Store(tmp_path/"db")
    store.set_setting("llm_model_file","original.gguf")
    target=tmp_path/"12b.gguf"
    target.write_bytes(b"GGUF")
    events=[]
    bridge=SimpleNamespace(window=SimpleNamespace(store=store),recording=False,
        controller=SimpleNamespace(change_turn_setting=store.set_setting),uiEvent=SimpleNamespace(emit=events.append))
    monkeypatch.setattr(gui.QFileDialog,"getOpenFileName",lambda *_:(str(target),""))
    gui.Bridge.chooseGemma12GGUF(bridge)
    assert store.get_settings()["llm_gemma12_file"]==str(target.resolve())
    assert store.get_settings()["llm_model_file"]=="original.gguf"
    assert store.get_settings()["llm_local_profile"]=="standard"


def test_cf_preflight_hash_config_and_merge_order(tmp_path):
    # Compile only the pure preflight function, never import FastAPI/torch worker.
    worker=Path(__file__).resolve().parents[2]/"local-tts/qwen_server.py"
    tree=ast.parse(worker.read_text(encoding="utf-8"))
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_cf_adapter_bytes")
    raw=b"synthetic no tensor data"
    scope={"Path":Path,"hashlib":hashlib,"json":json,"CF_SIZE":len(raw),
           "CF_SHA":hashlib.sha256(raw).hexdigest(),"CF_BASE_REVISION":"revision"}
    exec(compile(ast.Module(body=[func],type_ignores=[]),str(worker),"exec"),scope)
    path=tmp_path/"adapter.safetensors"
    path.write_bytes(raw)
    config=path.with_name("adapter.json")
    config.write_text(json.dumps({"r":16,"alpha":32,"base_revision":"revision"}))
    assert scope["_cf_adapter_bytes"](path)==raw
    path.write_bytes(b"x"*len(raw))
    with pytest.raises(RuntimeError,match="HASH_MISMATCH"):
        scope["_cf_adapter_bytes"](path)
    path.write_bytes(raw)
    config.write_text(json.dumps({"r":16,"alpha":64,"base_revision":"revision"}))
    with pytest.raises(RuntimeError,match="CONFIG_MISMATCH"):
        scope["_cf_adapter_bytes"](path)
    load=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_load_model")
    code=ast.unparse(load)
    assert code.index("_apply_cf050")<code.index("model.warmup")
    assert "local_files_only=True" in code

def test_cf_pinned_snapshot_does_not_require_readme_or_git_metadata(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    worker=Path(__file__).resolve().parents[2]/"local-tts/qwen_server.py"
    tree=ast.parse(worker.read_text(encoding="utf-8"))
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_cf_base_snapshot")
    root=tmp_path/"revision"
    calls=[]
    def snapshot(repo,**kw):
        calls.append((repo,kw))
        return str(root)
    monkeypatch.setitem(sys.modules,"huggingface_hub",SimpleNamespace(snapshot_download=snapshot))
    scope={"Path":Path,"CF_BASE_REVISION":"revision"}
    exec(compile(ast.Module(body=[func],type_ignores=[]),str(worker),"exec"),scope)
    with pytest.raises(RuntimeError,match="OFFLINE_MODEL_MISSING"):
        scope["_cf_base_snapshot"]()
    options=calls[-1][1]
    assert calls[-1][0]=="Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    assert options["local_files_only"] is True
    assert options["revision"]=="revision"
    files=options["allow_patterns"]
    assert "README.md" not in files and ".gitattributes" not in files
    assert "model.safetensors" in files
    assert "speech_tokenizer/model.safetensors" in files
    for name in files:
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b"test")
    assert scope["_cf_base_snapshot"]()==str(root)
    (root/"speech_tokenizer/model.safetensors").unlink()
    with pytest.raises(RuntimeError,match="OFFLINE_MODEL_MISSING"):
        scope["_cf_base_snapshot"]()
