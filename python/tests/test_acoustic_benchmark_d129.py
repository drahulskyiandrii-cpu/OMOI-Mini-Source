"""Aggregierte Benchmark-Auswertung ohne echte Modelle."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from benchmark_acoustic_cpu import read_manifest,benchmark
from omoi_mini.acoustic_worker import AcousticTiming
from omoi_mini.acoustic_policy import silero_evidence,ecapa_evidence,yamnet_evidence
from omoi_mini.recorder import Recorder
from omoi_mini.providers import ProviderFailure


def make_cases(tmp_path):
    rows=[]
    for i,(speech,lang,event) in enumerate([(True,"de","none"),(True,"ru","cough"),
                                            (False,"other","sneeze")]):
        path=tmp_path/f"synthetic-{i}.wav"
        path.write_bytes(Recorder._as_wav(bytes(32000),16000))
        rows.append(dict(audio=path.name,speech=speech,language=lang,event=event))
    manifest=tmp_path/"manifest.jsonl"
    manifest.write_text("\n".join(map(json.dumps,rows)),encoding="utf-8")
    return manifest,rows


def test_manifest_and_aggregate_metrics(tmp_path):
    manifest,_=make_cases(tmp_path)
    cases=read_manifest(manifest)
    calls=[]
    def fake(pcm):
        calls.append(len(pcm))
        if len(calls)==3:raise ProviderFailure("synthetic failure")
        return dict(silero=silero_evidence([.9]*8),
            ecapa=ecapa_evidence([-10]*44+[5]),
            yamnet=yamnet_evidence(dict(speech=.8,cough=.2,throatClearing=0,sneeze=0,frames=1)),
            **{name+"_timing":AcousticTiming(100,50) for name in ("silero","ecapa","yamnet")})
    result=benchmark(cases,"all",fake)
    assert result["cases"]==3 and result["completed"]==2 and result["failed"]==1
    assert result["lid"]["correct"]==1 and result["lid"]["wrong"]==1
    assert result["vad"]["false_reject"]==0
    assert result["events"]["speech_flagged"]==2
    assert result["events"]["false_alarm"]==1
    assert result["timings"]["ecapa"]["inference_rtf"]==.05
    assert result["timings"]["ecapa"]["load_p95_ms"]==100
    assert "synthetic" not in json.dumps(result)


@pytest.mark.parametrize("bad",["../outside.wav","missing.wav","foo.txt"])
def test_manifest_rejects_bad_paths(tmp_path,bad):
    manifest,rows=make_cases(tmp_path)
    rows[0]["audio"]=bad
    manifest.write_text("\n".join(map(json.dumps,rows)),encoding="utf-8")
    with pytest.raises(ValueError):read_manifest(manifest)


def test_manifest_rejects_duplicate_audio(tmp_path):
    manifest,rows=make_cases(tmp_path)
    rows[1]["audio"]=rows[0]["audio"]
    manifest.write_text("\n".join(map(json.dumps,rows)),encoding="utf-8")
    with pytest.raises(ValueError):read_manifest(manifest)


def test_cli_requires_approval_before_access():
    out=subprocess.run([sys.executable,str(ROOT/"scripts/benchmark_acoustic_cpu.py"),
                        "does-not-exist.jsonl","--mode","all"],capture_output=True,text=True,timeout=10)
    assert out.returncode==2
    assert "ACOUSTIC_MODEL_APPROVAL_REQUIRED" in out.stderr
