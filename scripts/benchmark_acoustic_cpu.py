"""Private, explizit freigegebene CPU-Qualifikation mit aggregierten Metriken."""
from __future__ import annotations
import argparse
import json
import sys
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from omoi_mini.acoustic_worker import MODES,qualify_pcm
from omoi_mini.providers import ProviderFailure
from omoi_mini.stt_metrics import percentile
from qualify_acoustic_cpu import read_pcm


EVENTS=("none","cough","throat-clearing","sneeze")
LANGUAGES=("de","ru","en","other")


def read_manifest(manifest,limit=100):
    root=manifest.resolve().parent
    cases=[]
    seen=set()
    with manifest.open(encoding="utf-8") as f:
        for i in range(limit+1):
            line=f.readline(8193)
            if not line:break
            if i>=limit or len(line)>8192:raise ValueError("Manifest limit")
            row=json.loads(line)
            if not isinstance(row,dict) or not isinstance(row.get("audio"),str):
                raise ValueError("Invalid manifest row")
            relative=Path(row["audio"])
            audio=root/relative
            if relative.is_absolute() or audio.suffix.lower()!=".wav":
                raise ValueError("Relative WAV path required")
            # Keine Symlink-/Traversal-Aufnahmen ausserhalb des Datensatzes.
            if ".." in relative.parts or not audio.resolve().is_relative_to(root):
                raise ValueError("Path outside dataset")
            audio=audio.resolve()
            if not audio.is_file() or audio in seen:raise ValueError("Missing or duplicate WAV")
            seen.add(audio)
            speech=row.get("speech")
            language=row.get("language")
            event=row.get("event")
            if type(speech) is not bool or language not in LANGUAGES or event not in EVENTS:
                raise ValueError("Invalid reference labels")
            cases.append((audio,speech,language,event))
    if not cases:raise ValueError("Empty manifest")
    return cases


def benchmark(cases,mode,analyze):
    selected=MODES[mode]
    totals={"cases":len(cases),"completed":0,"failed":0}
    if "silero" in selected:
        totals["vad"]={"cases":0,"false_accept":0,"false_reject":0}
    if "ecapa" in selected:
        totals["lid"]={"cases":0,"correct":0,"wrong":0,"abstain":0,
                       "by_language":{k:{"cases":0,"correct":0,"wrong":0,"abstain":0} for k in LANGUAGES}}
    if "yamnet" in selected:
        totals["events"]={"cases":0,"correct":0,"wrong":0,"missed":0,
                          "false_alarm":0,"speech_with_event":0,"speech_flagged":0}
    timings={name:{"load":[],"inference":[],"audio_ms":0} for name in selected}
    for audio,speech,language,event in cases:
        try:
            pcm=read_pcm(audio)
            result=analyze(pcm)
        except (ProviderFailure,ValueError,OSError,EOFError,wave.Error):
            totals["failed"]+=1
            continue
        totals["completed"]+=1
        if "silero" in selected:
            v=totals["vad"]
            actual=result["silero"].lane is not None
            v["cases"]+=1
            v["false_accept"]+=int(actual and not speech)
            v["false_reject"]+=int(not actual and speech)
        if "ecapa" in selected and speech:
            actual=result["ecapa"].language
            outcome="abstain" if actual is None else "correct" if actual==language else "wrong"
            for group in (totals["lid"],totals["lid"]["by_language"][language]):
                group["cases"]+=1
                group[outcome]+=1
        if "yamnet" in selected:
            e=totals["events"]
            actual=result["yamnet"].event or "none"
            e["cases"]+=1
            e["speech_with_event"]+=int(speech and event!="none")
            e["speech_flagged"]+=int(speech and actual!="none")
            outcome=("correct" if actual==event else "false_alarm" if event=="none"
                     else "missed" if actual=="none" else "wrong")
            e[outcome]+=1
        for name in selected:
            timing=result[name+"_timing"]
            values=timings[name]
            values["load"].append(timing.load_ms)
            values["inference"].append(timing.inference_ms)
            values["audio_ms"]+=len(pcm)/32
    totals["timings"]={}
    for name,values in timings.items():
        inference=values["inference"]
        totals["timings"][name]={
            "samples":len(inference),"load_p50_ms":percentile(values["load"],.5),
            "load_p95_ms":percentile(values["load"],.95),
            "inference_p50_ms":percentile(inference,.5),
            "inference_p95_ms":percentile(inference,.95),
            "inference_rtf":sum(inference)/values["audio_ms"] if values["audio_ms"] else None}
    return totals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path)
    parser.add_argument("--mode",choices=MODES,required=True)
    for name in ("silero","ecapa","yamnet"):parser.add_argument("--"+name,type=Path)
    parser.add_argument("--allow-models",action="store_true")
    args=parser.parse_args()
    if not args.allow_models:
        print("ACOUSTIC_MODEL_APPROVAL_REQUIRED",file=sys.stderr)
        return 2
    try:
        cases=read_manifest(args.manifest)
        options={name:getattr(args,name) for name in ("silero","ecapa","yamnet")}
        result=benchmark(cases,args.mode,lambda pcm:qualify_pcm(pcm,ROOT/"local-stt/acoustic_worker.mjs",
            mode=args.mode,allow_models=True,**options))
    except (ValueError,TypeError,KeyError,OSError,OverflowError):
        print("ACOUSTIC_BENCHMARK_INVALID",file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ACOUSTIC_BENCHMARK_CANCELLED",file=sys.stderr)
        return 130
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0 if result["failed"]==0 else 1


if __name__=="__main__":raise SystemExit(main())
