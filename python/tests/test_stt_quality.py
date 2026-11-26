import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.stt_quality import (
    PROFILES, hotwords, correct_domain, auto_language_issue,
    PartialStabilizer, decoder_options,
)
from omoi_mini.stt_domain_rules import RULES


def test_profile_settings_persist(tmp_path):
    path = tmp_path / "test.sqlite3"
    store = Store(path)
    assert store.get_settings()["stt_profile"] == "fast"
    store.set_setting("stt_profile", "balanced")
    store.set_setting("stt_hotwords", "Cisco, Packet Tracer")
    store.set_setting("stt_auto_language", True)
    assert Store(path).get_settings()["stt_auto_language"] is True
    assert Store(path).get_settings()["stt_profile"] == "balanced"
    assert Store(path).get_settings()["stt_hotwords"] == "Cisco, Packet Tracer"
    for key, value in (("stt_profile", "unknown"), ("stt_auto_language", 1),
                       ("stt_hotwords", "a"*501)):
        with pytest.raises(ValueError):
            store.set_setting(key, value)


def test_hotwords_budget_and_deduplication():
    terms = hotwords("cisco; CUDA; Packet Tracer; " + "\u044f"*300)
    assert len(terms.encode("utf-8")) <= 192
    assert terms.count("CUDA") == 1
    assert "cisco" in terms and "Packet Tracer" in terms


def test_domain_corrections_context_and_language():
    assert len(RULES) == 18
    assert correct_domain("Whispel Medium INT8", "de")[0] == "Whisper Medium INT8"
    assert correct_domain("Ich habe Anstellungen", "de")[0] == "Ich habe Anstellungen"
    assert correct_domain("Anstellungen Spracherkennung", "de")[0] == "Einstellungen Spracherkennung"
    assert correct_domain("Anstellungen Spracherkennung", "ru")[0] == "Anstellungen Spracherkennung"
    assert correct_domain("npm install OpenFlow", "en")[0] == "npm install OpenFlow"
    assert correct_domain("OpenWin runtime Vulkan", "de")[0] == "OpenVINO runtime Vulkan"


def test_auto_language_guard():
    assert auto_language_issue("<|ru|>", "Hello CUDA") is None
    assert auto_language_issue("de-DE", "Gr\u00fc\u00dfe") is None
    assert auto_language_issue("fr", "bonjour") == "unsupported-detected-language"
    assert auto_language_issue("de", "caf\u00e9 d\u00e9j\u00e0") == "unsupported-latin-orthography"
    assert auto_language_issue("en", "caf\u00e9") is None


def test_stabilizer_matches_words_and_resets_on_conflict():
    s = PartialStabilizer()
    assert s.update("Hallo Welt") == ("", "Hallo Welt")
    assert s.update("Hallo Welt, heute") == ("Hallo Welt,", " heute")
    assert s.update("Hallo Welt, heute Morgen") == ("Hallo Welt, heute", " Morgen")
    assert s.update("Guten Morgen") == ("", "Guten Morgen")
    assert s.update("Guten Morgen") == ("Guten Morgen", "")
    s.reset()
    assert s.update("Neu") == ("", "Neu")


@pytest.mark.parametrize("profile", PROFILES)
def test_decoder_profile_options(profile):
    partial = decoder_options(profile, partial=True)
    final = decoder_options(profile, partial=False)
    if profile == "fast":
        assert partial == final == {}
    else:
        assert partial["beam_size"] == 1
        assert not partial["vad_filter"] and not final["vad_filter"]
        assert partial["hotwords"]==final["hotwords"]==""
        assert final["beam_size"] == (5 if profile == "quality" else 1)


def test_controller_balanced_partial_and_quality_final(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    store = Store(tmp_path / "voice.sqlite3")
    store.set_setting("stt", "cuda")
    store.set_setting("stt_profile", "balanced")
    store.set_setting("stt_auto_language", True)
    ctrl = Controller(store)
    calls, events, final = [], [], []
    def fake(wav, lang, **kwargs):
        calls.append((lang, kwargs))
        return "Whispel Medium INT8"
    monkeypatch.setattr(ctrl.cuda_whisper, "transcribe", fake)
    monkeypatch.setattr(ctrl, "_dispatch_text", lambda turn,*args: final.append(args))
    ctrl.event.connect(lambda raw: events.append(json.loads(raw)))
    try:
        turn = ctrl.stop()
        ctrl._transcribe_partial(turn, b"fake", "de")
        ctrl._transcribe_partial(turn, b"fake", "de")
        partials = [e for e in events if e["type"] == "stt_partial"]
        assert partials[-1]["stable"] == "Whisper Medium INT8"
        assert not final
        assert calls[-1][0] == "auto" and calls[-1][1]["options"]["beam_size"] == 1
        assert calls[-1][1]["options"]["preferred_language"] == "de"
        store.set_setting("stt_profile", "quality")
        ctrl._transcribe(turn, b"fake")
        assert final == [("Whisper Medium INT8", "microphone")]
        assert calls[-1][1]["options"]["beam_size"] == 5
        assert calls[-1][1]["options"]["preferred_language"] == "de"
        assert not calls[-1][1]["options"]["vad_filter"]
    finally:
        ctrl.close()


def test_worker_protocol_without_importing_model_or_worker(monkeypatch):
    # Execute only the protocol function with synthetic dependency injection.
    source = Path(__file__).resolve().parents[2] / "local-stt" / "cuda_worker.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("main","choose_language","uncertain_segments")]
    calls, events = [], []
    def transcribe(samples, **kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(text="Hallo")], SimpleNamespace(language="de")
    requests = [
        {"id":1,"language":"de","audio":"fake"},
        {"id":2,"language":"auto","audio":"fake","options":decoder_options("quality",partial=False)},
        {"id":3,"language":"de","audio":"fake","options":{"beam_size":50}},
    ]
    namespace = dict(startup=lambda:(SimpleNamespace(transcribe=transcribe),0),
        emit=events.append, json=json, sys=SimpleNamespace(stdin=io.StringIO(
            "\n".join(map(json.dumps,requests)))),
        samples_from_wav=lambda audio:None,
        time=SimpleNamespace(perf_counter=lambda:0))
    exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),"exec"),namespace)
    namespace["main"]()
    assert calls[0] == dict(language="de",task="transcribe",beam_size=1,temperature=0,
                            condition_on_previous_text=False,vad_filter=False)
    assert calls[1]["language"] is None and calls[1]["beam_size"] == 5
    assert not calls[1]["vad_filter"] and "hotwords" not in calls[1]
    assert events[2]["language"] == "de"
    assert events[-1]["ok"] is False and len(calls)==2

@pytest.mark.parametrize("preferred",["de","ru"])
def test_auto_short_audio_uses_selected_language_without_detection(preferred):
    source=Path(__file__).resolve().parents[2]/"local-stt/cuda_worker.py"
    tree=ast.parse(source.read_text(encoding="utf-8"))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="choose_language")
    scope={}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    def fail(**kwargs):pytest.fail("short speech must not invoke language detection")
    model=SimpleNamespace(detect_language=fail)
    assert scope["choose_language"](model,range(32000),"auto",preferred)==preferred
    assert scope["choose_language"](model,range(160000),preferred,None)==preferred


@pytest.mark.parametrize("detected,confidence,probs,expected",[
    ("en",.55,[("en",.55),("de",.4)],"de"),
    ("en",.88,[("en",.88),("de",.09)],"en"),
    ("ru",.96,[("ru",.96),("de",.03)],"ru"),
    ("fr",.98,[("fr",.98),("de",.01)],"de"),
    ("en",.85,[("en",.85),("de",.70)],"de"),
])
def test_auto_long_audio_switches_only_with_high_confidence(detected,confidence,probs,expected):
    source=Path(__file__).resolve().parents[2]/"local-stt/cuda_worker.py"
    node=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(n,ast.FunctionDef) and n.name=="choose_language")
    scope={}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    model=SimpleNamespace(detect_language=lambda **kwargs:(detected,confidence,probs))
    assert scope["choose_language"](model,range(96000),"auto","de")==expected
