// Experimenteller CPU-Worker. Keine automatische Modellwahl oder Downloads.
import { createHash } from "node:crypto";
import { createReadStream, lstatSync } from "node:fs";
import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";

const MODELS = {
  silero: [1288203, "7776b81ad1b0350c15d7f1555943b9232eb53e9ca5d989c6d0cea9ebc8664d87"],
  ecapa: [84181263, "a25d6ff7dc5a234853b4d854aff76785093b46a1157d1dbc948d1e38b27746af"],
  yamnet: [16093355, "d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e"]
};
const MAX_REQUEST = 1300000;

export async function verifyModel(file, size, sha256) {
  if (typeof file !== "string" || !isAbsolute(file)) throw Error("MODEL_PATH");
  const stat = lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== size) throw Error("MODEL_SIZE");
  const hash = createHash("sha256");
  let bytes = 0;
  for await (const part of createReadStream(file)) {
    bytes += part.length;
    if (bytes > size) throw Error("MODEL_SIZE");
    hash.update(part);
  }
  if (bytes !== size || hash.digest("hex") !== sha256) throw Error("MODEL_HASH");
}

export function decodeRequest(request) {
  if (!request || request.schema !== 1 || request.id !== 1 ||
      !["silero", "ecapa", "yamnet", "both", "all"].includes(request.mode) ||
      typeof request.pcm !== "string" || request.pcm.length > 1280000)
    throw Error("REQUEST_INVALID");
  const raw = Buffer.from(request.pcm, "base64");
  if (raw.toString("base64") !== request.pcm || raw.length % 2 ||
      raw.length < 3200 || raw.length > 960000) throw Error("PCM_INVALID");
  const samples = new Float32Array(raw.length / 2);
  for (let i = 0; i < samples.length; i++) samples[i] = raw.readInt16LE(i * 2) / 32768;
  return samples;
}

function tensorData(tensor, length) {
  const data = tensor?.getData();
  if (!(data instanceof Float32Array) || data.length !== length ||
      !data.every(Number.isFinite)) throw Error("TENSOR_CONTRACT");
  return data;
}

export function sileroProbabilities(model, Tensor, samples) {
  const infer = model.createInferRequest();
  let state = new Float32Array(256), context = new Float32Array(64);
  const probabilities = [];
  for (let offset = 0; offset < samples.length; offset += 512) {
    const frame = new Float32Array(512);
    frame.set(samples.subarray(offset, offset + 512));
    const input = new Float32Array(576);
    input.set(context);
    input.set(frame, 64);
    const outputs = Object.values(infer.infer({
      input: new Tensor("f32", [1, 576], input),
      state: new Tensor("f32", [2, 1, 128], state)
    }));
    if (outputs.length !== 2) throw Error("SILERO_OUTPUT");
    const probability = tensorData(outputs.find(t => t.getData().length === 1), 1)[0];
    if (probability < 0 || probability > 1) throw Error("SILERO_PROBABILITY");
    state = tensorData(outputs.find(t => t.getData().length === 256), 256).slice();
    context = frame.slice(-64);
    probabilities.push(probability);
  }
  return probabilities;
}

export function ecapaLogits(model, Tensor, samples) {
  const outputs = Object.values(model.createInferRequest().infer({
    waveform: new Tensor("f32", [1, samples.length], samples)
  }));
  if (outputs.length !== 1) throw Error("ECAPA_OUTPUT");
  return Array.from(tensorData(outputs[0], 45));
}

// YAMNet: AudioSet-Scores, keine Rauschunterdrueckung.
export function yamnetScores(model, Tensor, samples) {
  const outputs = model.createInferRequest().infer({
    waveform: new Tensor("f32", [samples.length], samples)
  });
  const values = outputs.output_0?.getData();
  if (!(values instanceof Float32Array) || !values.length ||
      values.length % 521 || values.length > 521 * 128 ||
      !values.every(v => Number.isFinite(v) && v >= 0 && v <= 1))
    throw Error("YAMNET_OUTPUT");
  const scores = {speech:0, cough:0, throatClearing:0, sneeze:0, frames:values.length/521};
  for (let offset=0; offset<values.length; offset+=521) {
    for (const [key,index] of [["speech",0],["cough",42],["throatClearing",43],["sneeze",44]])
      scores[key]=Math.max(scores[key],values[offset+index]);
  }
  return scores;
}

async function main() {
  const [approval, sileroPath, ecapaPath, yamnetPath] = process.argv.slice(2);
  if (approval !== "--allow-models") throw Error("EXPLICIT_APPROVAL_REQUIRED");
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_REQUEST) throw Error("REQUEST_LIMIT");
    chunks.push(chunk);
  }
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const samples = decodeRequest(request);
  const selected = request.mode === "all" ? ["silero", "ecapa", "yamnet"] :
    request.mode === "both" ? ["silero", "ecapa"] : [request.mode];
  const paths = {silero: sileroPath, ecapa: ecapaPath, yamnet: yamnetPath};
  // Alle angeforderten Artefakte vor dem ersten nativen Import pruefen.
  for (const name of selected) await verifyModel(paths[name], ...MODELS[name]);
  const imported = await import("openvino-node");
  const ov = [imported.addon, imported.default?.addon, imported.default, imported]
    .find(m => typeof m?.Core === "function" && typeof m?.Tensor === "function");
  if (!ov) throw Error("OPENVINO_API");
  const core = new ov.Core();
  const result = {schema:1, id:1, ok:true, device:"CPU", samples:samples.length};
  for (const name of selected) {
    const started = performance.now();
    const model = await core.compileModel(paths[name], "CPU", {INFERENCE_PRECISION_HINT:"f32"});
    result[name + "LoadMs"] = Math.round(performance.now() - started);
    const inferenceStart = performance.now();
    result[name] = name === "silero" ? sileroProbabilities(model, ov.Tensor, samples)
      : name === "ecapa" ? ecapaLogits(model, ov.Tensor, samples)
      : yamnetScores(model, ov.Tensor, samples);
    result[name + "Ms"] = Math.round(performance.now() - inferenceStart);
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { await main(); }
  catch {
    // Keine Audio-, Pfad- oder Ausnahmedetails auf stdout/stderr.
    process.stdout.write(JSON.stringify({schema:1,id:1,ok:false,error:"ACOUSTIC_FAILED"})+"\n");
    process.exitCode = 2;
  }
}
