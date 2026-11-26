"""Private JSONL-Transkripte auswerten; Ausgabe nur aggregierte Zahlen."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
from omoi_mini.stt_metrics import evaluate


def read_rows(path):
    with path.open(encoding="utf-8") as handle:
        for i in range(10001):
            line=handle.readline(65537)
            if not line:return
            if i>=10000 or len(line)>65536:raise ValueError("Benchmark-Limit")
            row=json.loads(line)
            if not isinstance(row,dict):raise ValueError("JSON-Objekt erwartet")
            yield row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    args=parser.parse_args()
    try:report=evaluate(read_rows(args.input))
    except (ValueError,TypeError,KeyError,OSError,OverflowError):
        print("STT_BENCHMARK_INPUT_INVALID",file=sys.stderr)
        return 2
    print(json.dumps({"normalization":"nfc-lower-alphanumeric; CER without whitespace",
                      "groups":report},ensure_ascii=True,indent=2,allow_nan=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
