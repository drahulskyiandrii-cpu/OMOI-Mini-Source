"""Reproduzierbare Textmetriken ohne private Daten oder Modelle."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from omoi_mini.stt_metrics import ErrorTotals,edit_distance,evaluate,words


def row(case,reference,hypothesis,**extra):
    return dict(case=case,engine="synthetic",language="de",
                reference=reference,hypothesis=hypothesis,**extra)


def test_normalization_preserves_german_and_russian_letters():
    assert words("Guten, MORGEN!") == ["guten","morgen"]
    assert words("Gru\u0308\u00dfe") == ["gr\u00fc\u00dfe"]
    assert words("\u0401\u0436, \u0415\u0436") == ["\u0451\u0436","\u0435\u0436"]
    assert words("Stra\u00dfe")!=words("Strasse")


@pytest.mark.parametrize("a,b,n",[("abc","abc",0),("abc","ab",1),("a","aaa",2),
                                     ("abc","axc",1),("","abc",3)])
def test_distance(a,b,n):
    assert edit_distance(a,b)==edit_distance(b,a)==n


def test_aggregate_weighted_metrics_and_silence():
    totals=ErrorTotals()
    totals.add("one two three","one two four")
    totals.add("yes","")
    totals.add("","hallucination")
    result=totals.report()
    assert result["wer"]==.5
    assert result["silence_cases"]==result["silence_hallucinations"]==1
    assert ErrorTotals().report()["wer"] is None


def test_cer_and_wer_can_exceed_one():
    t=ErrorTotals()
    t.add("a","a bb cc")
    assert t.report()["wer"]==2
    assert t.report()["cer"]==4


def test_paired_corrections_and_failures_not_hidden():
    data=[row("a","Hallo Welt","Hallo Geld",corrected="Hallo Welt",latency_ms=100,audio_ms=500),
          row("b","Hallo","Hallo",latency_ms=200,audio_ms=500),
          row("c","Welt","",ok=False)]
    group=evaluate(data)[0]
    assert group["failed"]==1 and group["raw"]["wer"]==.5
    assert group["paired_raw"]["wer"]==.5 and group["corrected"]["wer"]==0
    assert group["p50_ms"]==100 and group["p95_ms"]==200
    assert group["rtf"]==.3


def test_duplicate_case_and_invalid_latency_rejected():
    r=row("a","Hallo","Hallo")
    with pytest.raises(ValueError):evaluate([r,r])
    with pytest.raises(ValueError):evaluate([{**r,"latency_ms":float("inf")}])


def test_cli_emits_only_aggregates(tmp_path):
    path=tmp_path/"private.jsonl"
    path.write_text(json.dumps(row("a","private reference","private hypothesis")),encoding="utf-8")
    script=Path(__file__).resolve().parents[2]/"scripts/evaluate_stt_text.py"
    out=subprocess.run([sys.executable,str(script),str(path)],capture_output=True,text=True,timeout=10)
    assert out.returncode==0 and "private" not in out.stdout
    assert json.loads(out.stdout)["groups"][0]["raw"]["wer"]==.5
