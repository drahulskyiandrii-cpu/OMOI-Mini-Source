"""WAV-Vertraege der beiden Worker ohne Import oder Modellstart."""
import ast
import base64
import io
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]


def wav():
    out=io.BytesIO()
    with wave.open(out,"wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01"*1600)
    return out.getvalue()


def cuda_parser():
    source=ROOT/"local-stt/cuda_worker.py"
    node=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
              if isinstance(n,ast.FunctionDef) and n.name=="samples_from_wav")
    scope=dict(base64=base64,io=io,wave=wave,np=np)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),scope)
    return scope["samples_from_wav"]


def test_cuda_wav_truncation_rejected_without_gpu():
    parse=cuda_parser()
    raw=wav()
    assert len(parse(base64.b64encode(raw).decode()))==1600
    with pytest.raises(ValueError,match="TRUNCATED_PCM"):
        parse(base64.b64encode(raw[:-2]).decode())


def test_openvino_wav_parser_without_importing_node_model():
    node=shutil.which("node")
    if node is None:pytest.skip("Node.js nicht installiert")
    source=(ROOT/"local-stt/worker.mjs").read_text(encoding="utf-8")
    parser=source[source.index("function wavToFloat32("):source.index("let pipeline;")]
    raw=wav()
    odd=bytearray(raw)
    struct.pack_into("<I",odd,40,3199)
    script=parser+"""
const valid=Buffer.from(process.argv[1],"base64");
if(wavToFloat32(valid).length!==1600) throw Error("valid audio failed");
for(const raw of [valid.subarray(0,-2),Buffer.from(process.argv[2],"base64")]){
  let rejected=false;
  try{wavToFloat32(raw)}catch{rejected=true}
  if(!rejected)throw Error("invalid audio accepted");
}
"""
    result=subprocess.run([node,"-e",script,base64.b64encode(raw).decode(),
                           base64.b64encode(odd).decode()],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
