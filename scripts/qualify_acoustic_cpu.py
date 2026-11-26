"""Manueller CPU-Test; kein automatischer Start oder Modell-Download."""
import argparse
from dataclasses import asdict
import json
import sys
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
from omoi_mini.acoustic_worker import qualify_pcm
from omoi_mini.providers import ProviderFailure


def read_pcm(path):
    with wave.open(str(path),"rb") as wav:
        frames=wav.getnframes()
        if (wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000
                or wav.getcomptype()!="NONE" or not 1600<=frames<=480000):
            raise ValueError("Invalid WAV")
        pcm=wav.readframes(frames)
        if len(pcm)!=frames*2:raise ValueError("Truncated WAV")
    return pcm


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio",type=Path)
    parser.add_argument("--mode",choices=("silero","ecapa","yamnet","both","all"),required=True)
    parser.add_argument("--silero",type=Path)
    parser.add_argument("--ecapa",type=Path)
    parser.add_argument("--yamnet",type=Path)
    parser.add_argument("--allow-models",action="store_true")
    args=parser.parse_args()
    if not args.allow_models:
        print("ACOUSTIC_MODEL_APPROVAL_REQUIRED",file=sys.stderr)
        return 2
    try:
        result=qualify_pcm(read_pcm(args.audio),ROOT/"local-stt/acoustic_worker.mjs",
                           mode=args.mode,silero=args.silero,ecapa=args.ecapa,yamnet=args.yamnet,
                           allow_models=True)
    except (ProviderFailure,ValueError,OSError,EOFError,wave.Error):
        print("ACOUSTIC_TEST_FAILED",file=sys.stderr)
        return 2
    print(json.dumps({k:asdict(v) for k,v in result.items()},indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
