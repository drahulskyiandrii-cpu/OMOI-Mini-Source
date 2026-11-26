"""Akustikregeln und Aufnahmegrenzen ohne echte Modelle/Geraete."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from omoi_mini.acoustic_policy import silero_evidence,ecapa_evidence
from omoi_mini.recorder import Recorder
from omoi_mini.stt_audio_guard import analyze,analyze_wav


@pytest.mark.parametrize("probabilities,lane",[
    ([.6]*8,"sustained"),
    ([.91,.88,.8,.75],"short-high-confidence"),
    ([.91,.1]*8,None),
    ([.99]*3,None),
    ([.7]*4,None),
    ([0.]*938,None),
])
def test_vad_lanes(probabilities,lane):
    assert silero_evidence(probabilities).lane==lane


@pytest.mark.parametrize("bad",[[],[.5]*939,[float("nan")],[1.1],[-.1],[True]])
def test_vad_rejects_invalid_evidence(bad):
    with pytest.raises(ValueError):silero_evidence(bad)


def logits(index):
    values=[-10.]*45
    values[index]=5.
    return values


@pytest.mark.parametrize("index,language",[(22,"ru"),(24,"en"),(44,"de"),(0,None)])
def test_language_evidence_global_top_class(index,language):
    assert ecapa_evidence(logits(index)).language==language


def test_language_abstains_for_ties_and_invalid_logits():
    assert ecapa_evidence([0.]*45).reason=="uncertain" or ecapa_evidence([0.]*45).language is None
    a=logits(44)
    a[22]=5.
    assert ecapa_evidence(a).language is None
    for bad in ([0.]*44,[float("nan")]*45,[True]*45):
        with pytest.raises(ValueError):ecapa_evidence(bad)


def test_acoustic_evaluation_aggregates_only(tmp_path):
    p=tmp_path/"private.jsonl"
    rows=[{"case":"secret-case","vad":[.6]*8,"speech":True,"logits":logits(44),"language":"de"},
          {"vad":[.1]*8,"speech":True,"logits":logits(0),"language":"other"},
          {"vad":[.9]*4,"speech":False,"logits":logits(22),"language":"en"}]
    p.write_text("\n".join(map(json.dumps,rows)),encoding="utf-8")
    script=Path(__file__).resolve().parents[2]/"scripts/evaluate_acoustic_evidence.py"
    out=subprocess.run([sys.executable,str(script),str(p)],capture_output=True,text=True,timeout=10)
    assert out.returncode==0 and "secret-case" not in out.stdout
    assert json.loads(out.stdout)=={"vad_cases":3,"vad_false_reject":1,"vad_false_accept":1,
            "lid_cases":3,"lid_correct":1,"lid_wrong":1,"lid_abstain":1}


def test_late_microphone_callback_never_enters_new_recording(monkeypatch):
    callbacks=[]
    class Stream:
        def __init__(self,**kwargs):callbacks.append(kwargs["callback"])
        def start(self):pass
        def stop(self):pass
        def close(self):pass
    fake=SimpleNamespace(check_input_settings=lambda **kw:None,InputStream=Stream)
    monkeypatch.setitem(sys.modules,"sounddevice",fake)
    r=Recorder()
    samples=np.full((16000,1),1000,dtype=np.int16)
    r.start()
    callbacks[0](samples,16000,None,None)
    assert r.stop()
    callbacks[0](samples,16000,None,None)
    assert r.snapshot()==b""
    r.start()
    callbacks[0](samples,16000,None,None)
    assert r.snapshot()==b""
    callbacks[1](samples,16000,None,None)
    assert r.snapshot()
    assert r.stop()


def test_failed_stream_constructor_discards_late_frames(monkeypatch):
    callbacks=[]
    def start(**kw):
        callbacks.append(kw["callback"])
        raise OSError("synthetic")
    monkeypatch.setitem(sys.modules,"sounddevice",SimpleNamespace(
        check_input_settings=lambda **kw:None,InputStream=start))
    r=Recorder()
    with pytest.raises(OSError):r.start()
    callbacks[0](np.ones((16000,1),dtype=np.int16),16000,None,None)
    assert r.snapshot()==b""


@pytest.mark.parametrize("bad",[np.nan,np.inf,1.1])
def test_guard_rejects_invalid_float_audio(bad):
    with pytest.raises(ValueError):analyze(np.array([bad]))


def test_guard_rejects_truncated_pcm():
    data=Recorder._as_wav(np.zeros(16000,dtype="<i2").tobytes(),16000)
    with pytest.raises(ValueError,match="Truncated"):analyze_wav(data[:-2])


@pytest.mark.parametrize("scores,event",[
    ({"speech":.9,"cough":.15,"throatClearing":.6,"sneeze":.9,"frames":2},"cough"),
    ({"speech":.9,"cough":.1,"throatClearing":.15,"sneeze":.9,"frames":2},"throat-clearing"),
    ({"speech":.9,"cough":.1,"throatClearing":.1,"sneeze":.25,"frames":2},"sneeze"),
    ({"speech":.9,"cough":.1,"throatClearing":.1,"sneeze":.24,"frames":2},None),
])
def test_yamnet_event_is_diagnostic_not_veto(scores,event):
    from omoi_mini.acoustic_policy import yamnet_evidence
    evidence=yamnet_evidence(scores)
    assert evidence.event==event and evidence.speech_score==.9
    assert not hasattr(evidence,"reject")


def test_yamnet_invalid_scores():
    from omoi_mini.acoustic_policy import yamnet_evidence
    good=dict(speech=.8,cough=.1,throatClearing=.1,sneeze=.1,frames=1)
    for bad in (dict(good,speech=float("nan")),dict(good,frames=0),dict(good,cough=1.2)):
        with pytest.raises(ValueError):yamnet_evidence(bad)
