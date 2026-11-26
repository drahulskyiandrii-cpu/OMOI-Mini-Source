"""Offline-Silero/ECAPA-Policyvergleich: private JSONL-Werte, nur Summen."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
from omoi_mini.acoustic_policy import silero_evidence,ecapa_evidence


def evaluate(rows):
    totals={"vad_cases":0,"vad_false_reject":0,"vad_false_accept":0,
            "lid_cases":0,"lid_correct":0,"lid_wrong":0,"lid_abstain":0}
    for row in rows:
        if not isinstance(row,dict):raise ValueError("Invalid row")
        if "vad" in row:
            expected=row["speech"]
            if type(expected) is not bool:raise ValueError("Boolean expected")
            actual=silero_evidence(row["vad"]).lane is not None
            totals["vad_cases"]+=1
            totals["vad_false_reject"]+=int(expected and not actual)
            totals["vad_false_accept"]+=int(not expected and actual)
        if "logits" in row:
            expected=row["language"]
            if expected not in ("de","ru","en","other"):raise ValueError("Invalid language")
            actual=ecapa_evidence(row["logits"]).language
            totals["lid_cases"]+=1
            if actual is None:totals["lid_abstain"]+=1
            elif actual==expected:totals["lid_correct"]+=1
            else:totals["lid_wrong"]+=1
    return totals


def read_rows(path):
    with path.open(encoding="utf-8") as f:
        for i in range(10001):
            line=f.readline(65537)
            if not line:return
            if i>=10000 or len(line)>65536:raise ValueError("Input limit")
            yield json.loads(line)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    args=parser.parse_args()
    try:result=evaluate(read_rows(args.input))
    except (ValueError,TypeError,KeyError,OSError,OverflowError):
        print("ACOUSTIC_INPUT_INVALID",file=sys.stderr)
        return 2
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
