"""Nur synthetische Prozesse/Tensoren, keine Modellinferenz."""
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import pytest
from omoi_mini import acoustic_worker as worker
from omoi_mini.providers import ProviderFailure

ROOT=Path(__file__).resolve().parents[2]


def test_model_approval_is_required_before_spawn(monkeypatch):
    monkeypatch.setattr(worker,"spawn_external",lambda *a,**kw:pytest.fail("unexpected spawn"))
    with pytest.raises(ProviderFailure,match="freigegeben"):
        worker.qualify_pcm(bytes(3200),Path("missing"),mode="silero")


@pytest.mark.parametrize("mode",["silero","ecapa","both","yamnet","all"])
def test_synthetic_cpu_worker_response_and_cleanup(tmp_path,monkeypatch,mode):
    file=tmp_path/"fake.onnx";file.write_bytes(b"not a model")
    script=tmp_path/"fake.mjs";script.write_text("// no execution")
    processes=[]
    def spawn(*a,**kw):
        code=("import sys,json; r=json.load(sys.stdin); "
              "print(json.dumps(dict(schema=1,id=1,ok=True,device='CPU',samples=1600,"
              "silero=[.9]*4,ecapa=[-10]*44+[5],yamnet=dict(speech=.8,cough=.2,throatClearing=.0,sneeze=.0,frames=1),"
              "sileroLoadMs=20,sileroMs=10,ecapaLoadMs=30,ecapaMs=15,yamnetLoadMs=40,yamnetMs=20)),flush=True)")
        p=subprocess.Popen([sys.executable,"-u","-c",code],**kw)
        processes.append(p)
        return p
    monkeypatch.setattr(worker,"spawn_external",spawn)
    result=worker.qualify_pcm(bytes(3200),script,mode=mode,silero=file,ecapa=file,yamnet=file,allow_models=True)
    if mode in ("silero","both","all"):assert result["silero"].lane=="short-high-confidence"
    if mode in ("ecapa","both","all"):assert result["ecapa"].language=="de"
    if mode in ("yamnet","all"):assert result["yamnet"].event=="cough"
    for name in worker.MODES[mode]:assert result[name+"_timing"].inference_ms>0
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize("cancel",[False,True])
def test_blocked_owned_worker_reaped(tmp_path,monkeypatch,cancel):
    file=tmp_path/"fake.onnx";file.write_bytes(b"not a model")
    script=tmp_path/"fake.mjs";script.write_text("// no execution")
    processes=[]
    stop=threading.Event()
    def spawn(*a,**kw):
        p=subprocess.Popen([sys.executable,"-u","-c","import time; time.sleep(30)"],**kw)
        processes.append(p)
        if cancel:stop.set()
        return p
    monkeypatch.setattr(worker,"spawn_external",spawn)
    with pytest.raises(ProviderFailure,match="abgebrochen" if cancel else "Zeitlimit"):
        worker.qualify_pcm(bytes(3200),script,mode="silero",silero=file,allow_models=True,
                           is_active=lambda:not stop.is_set(),timeout=.2)
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_wrong_frame_count_and_device_rejected():
    reply=dict(schema=1,id=1,ok=True,device="CPU",samples=1600,silero=[.9]*3)
    with pytest.raises(ValueError):worker.validate_result(reply,"silero",1600)
    with pytest.raises(ValueError):worker.validate_result({**reply,"device":"GPU"},"silero",1600)


def test_node_mock_tensor_contracts():
    node=shutil.which("node")
    if node is None:pytest.skip("Node.js not available")
    result=subprocess.run([node,"--test",str(ROOT/"local-stt/acoustic_worker.test.mjs")],
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_node_cli_without_approval_cannot_load_models():
    node=shutil.which("node")
    if node is None:pytest.skip("Node.js not available")
    result=subprocess.run([node,str(ROOT/"local-stt/acoustic_worker.mjs")],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==2
    assert json.loads(result.stdout)["error"]=="ACOUSTIC_FAILED"


def test_manual_cli_requires_approval_before_reading_audio():
    script=ROOT/"scripts/qualify_acoustic_cpu.py"
    out=subprocess.run([sys.executable,str(script),"nonexistent.wav","--mode","both"],
                       capture_output=True,text=True,timeout=10)
    assert out.returncode==2
    assert "ACOUSTIC_MODEL_APPROVAL_REQUIRED" in out.stderr
