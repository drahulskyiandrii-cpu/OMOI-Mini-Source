import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { decodeRequest, verifyModel, sileroProbabilities, ecapaLogits, yamnetScores } from "./acoustic_worker.mjs";

class Tensor {
  constructor(type, shape, data) { this.shape=shape; this.data=data; }
  getData() { return this.data; }
}

test("PCM validation and signed little endian conversion",()=>{
  const raw=Buffer.alloc(3200);
  raw.writeInt16LE(-32768,0);
  raw.writeInt16LE(16384,2);
  const r={schema:1,id:1,mode:"both",pcm:raw.toString("base64")};
  const samples=decodeRequest(r);
  assert.equal(samples.length,1600);
  assert.equal(samples[0],-1);
  assert.equal(samples[1],.5);
  for(const bad of [{...r,id:2},{...r,pcm:r.pcm+"!"},{...r,mode:"auto"},
                    {...r,pcm:Buffer.alloc(3201).toString("base64")}])
    assert.throws(()=>decodeRequest(bad));
});

test("Silero context and recurrent state reset for each utterance",()=>{
  let calls=0;
  const model={createInferRequest(){let frame=0;return {infer({input,state}){
    assert.deepEqual(input.shape,[1,576]);
    assert.deepEqual(state.shape,[2,1,128]);
    assert.equal(state.data[0],frame);
    assert.equal(input.data[0],frame===0?0:.25);
    frame++;calls++;
    return {output:new Tensor("f32",[1],new Float32Array([.75])),
      stateN:new Tensor("f32",[256],new Float32Array(256).fill(frame))};
  }}}};
  const samples=new Float32Array(1600).fill(.25);
  assert.deepEqual(sileroProbabilities(model,Tensor,samples),[.75,.75,.75,.75]);
  assert.equal(sileroProbabilities(model,Tensor,samples).length,4);
  assert.equal(calls,8);
});

test("ECAPA waveform shape and strict output length",()=>{
  const samples=new Float32Array(1600);
  const model={createInferRequest:()=>({infer({waveform}){
    assert.deepEqual(waveform.shape,[1,1600]);
    return {logits:new Tensor("f32",[1,45],new Float32Array(45))};
  }})};
  assert.equal(ecapaLogits(model,Tensor,samples).length,45);
  assert.throws(()=>ecapaLogits({createInferRequest:()=>({infer:()=>({x:new Tensor("f32",[1],new Float32Array(1))})})},Tensor,samples));
});

test("model hash verified before native runtime is used",async()=>{
  const root=await mkdtemp(join(tmpdir(),"omoi-acoustic-test-"));
  try {
    const file=join(root,"fake.onnx");
    const data=Buffer.from("synthetic-not-a-model");
    await writeFile(file,data);
    const hash=createHash("sha256").update(data).digest("hex");
    await verifyModel(file,data.length,hash);
    await assert.rejects(verifyModel(file,data.length,"0".repeat(64)));
    await assert.rejects(verifyModel(file,data.length+1,hash));
  } finally { await rm(root,{recursive:true,force:true}); }
});


test("YAMNet named score output and maxima without embeddings",()=>{
  const samples=new Float32Array(1600);
  const values=new Float32Array(1042);
  values[0]=.5; values[42]=.25; values[521+43]=.75; values[521+44]=.5;
  const model={createInferRequest:()=>({infer({waveform}){
    assert.deepEqual(waveform.shape,[1600]);
    return {output_0:new Tensor("f32",[2,521],values),
            output_1:new Tensor("f32",[1],new Float32Array(1))};
  }})};
  assert.deepEqual(yamnetScores(model,Tensor,samples),
    {speech:.5,cough:.25,throatClearing:.75,sneeze:.5,frames:2});
  values[1]=NaN;
  assert.throws(()=>yamnetScores(model,Tensor,samples));
});
