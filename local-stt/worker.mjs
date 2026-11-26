// Isolierter, an stdin/stdout gekoppelter OpenVINO-Whisper-Prozess.
// Nur JSON-Protokoll auf stdout, keine privaten Audio-/Transkriptinhalte in Logs.
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { createInterface } from "node:readline";
import { ASRPipeline } from "openvino-genai-node";

// Modellpfad ausschließlich vom Mini-Adapter bzw. Benutzer konfigurieren.
// Kein stiller Zugriff auf die private Haupt-OMOI-Modellbibliothek.
const root = process.env.OMOI_MINI_STT_MODEL;
if (!root) {
  process.stdout.write(JSON.stringify({event:"fatal",error:"STT_MODEL_PATH_NOT_CONFIGURED"})+"\n");
  process.exit(2);
}
const device = (process.env.OMOI_MINI_STT_DEVICE || "CPU").toUpperCase();
const emit = (value) => process.stdout.write(JSON.stringify(value)+"\n");

function wavToFloat32(raw) {
  if (raw.length < 44 || raw.toString("ascii",0,4)!=="RIFF" || raw.toString("ascii",8,12)!=="WAVE")
    throw new Error("INVALID_WAV");
  let format=null, data=null, cursor=12;
  while (cursor+8 <= raw.length) {
    const name=raw.toString("ascii",cursor,cursor+4);
    const size=raw.readUInt32LE(cursor+4);
    const start=cursor+8, end=start+size;
    if(end>raw.length) throw new Error("TRUNCATED_WAV_CHUNK");
    if (name==="fmt " && end-start>=16) format={
      kind:raw.readUInt16LE(start),channels:raw.readUInt16LE(start+2),
      rate:raw.readUInt32LE(start+4),bits:raw.readUInt16LE(start+14)
    };
    if (name==="data") data=raw.subarray(start,end);
    cursor=start+size+(size&1);
  }
  if (!format || !data || format.kind!==1 || format.channels!==1 ||
      format.rate!==16000 || format.bits!==16 || data.length%2 || data.length>16000*30*2)
    throw new Error("UNSUPPORTED_AUDIO_FORMAT");
  const count=Math.floor(data.length/2);
  const result=new Float32Array(count);
  for(let i=0;i<count;i++) result[i]=data.readInt16LE(i*2)/32768;
  return result;
}
let pipeline;
try {
  const props={};
  if(device==="NPU"){props.STATIC_PIPELINE=true;props.CACHE_DIR=path.join(os.tmpdir(),"omoi-mini-whisper-cache");}
  const started=performance.now();
  pipeline=await ASRPipeline(root,device,props);
  emit({event:"ready",device,loadMs:Math.round(performance.now()-started)});
} catch(err) {
  emit({event:"fatal",error:"STT_MODEL_OR_RUNTIME_UNAVAILABLE"});
  process.exit(2);
}
for await (const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
  let id=null;
  try {
    if(line.length>24*1024*1024) throw new Error("REQUEST_TOO_LARGE");
    const request=JSON.parse(line);
    id=request.id;
    if(!Number.isSafeInteger(id) || id<0) throw new Error("INVALID_REQUEST_ID");
    if(!["de","ru"].includes(request.language)) throw new Error("UNSUPPORTED_LANGUAGE");
    if(typeof request.audio!=="string") throw new Error("INVALID_AUDIO");
    const raw=Buffer.from(request.audio,"base64");
    if(raw.toString("base64")!==request.audio) throw new Error("INVALID_BASE64");
    const samples=wavToFloat32(raw);
    if(samples.length<1600 || samples.length>16000*30) throw new Error("INVALID_DURATION");
    const language=request.language;
    const started=performance.now();
    const result=await pipeline.generate(samples,{generationConfig:{
      task:"transcribe",return_timestamps:false,max_new_tokens:160,
      language:`<|${language}|>`
    }});
    emit({id,ok:true,text:String(result.texts?.[0]||"").trim(),
      device,wallMs:Math.round(performance.now()-started)});
  } catch(err) {
    emit({id,ok:false,error:"STT_FAILED"});
  }
}
