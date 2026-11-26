"""Harte Satzgrenzen und strikte WAV-Vertraege."""
import io
import wave
from types import SimpleNamespace
import httpx
import pytest
from omoi_mini.pcm_wave import read_pcm_wav,validate_pcm_wav
from omoi_mini.streaming import SpeechChunker
from omoi_mini.recorder import Recorder
from omoi_mini.local_tts import local_tts_wav
from omoi_mini.providers import ProviderFailure
from omoi_mini.audio import AudioPlayer


@pytest.mark.parametrize("text",["x"*4001,"wort "*900,"x"*100+" "+("wort "*700)+". "])
def test_tts_chunk_length_is_bounded(text):
    c=SpeechChunker()
    chunks=[]
    for offset in range(0,len(text),37):chunks.extend(c.feed(text[offset:offset+37]))
    chunks.extend(c.finish())
    assert all(0<len(chunk)<=170 for chunk in chunks)
    assert "".join("".join(chunks).split())=="".join(text.split())


def test_short_sentence_boundary_and_long_following_sentence():
    c=SpeechChunker()
    first="Ein VLAN trennt Netzwerke."
    chunks=c.feed(first+" "+("Wort "*100)+". ")
    chunks+=c.finish()
    assert chunks[0]==first
    assert all(len(x)<=170 for x in chunks)


@pytest.mark.parametrize("bad",[b"RIFFxxxxWAVE"+b"x"*50,
    Recorder._as_wav(bytes(1000),16000)[:-2],
    Recorder._as_wav(b"",16000)])
def test_invalid_wav_rejected(bad):
    with pytest.raises((ValueError,EOFError,wave.Error)):validate_pcm_wav(bad)


def test_qwen_rejects_truncated_wav(monkeypatch):
    raw=Recorder._as_wav(bytes(3200),24000)[:-2]
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:real(
        transport=httpx.MockTransport(lambda req:httpx.Response(200,content=raw)),**kw))
    with pytest.raises(ProviderFailure,match="WAV"):local_tts_wav("Hallo")


def test_player_skips_invalid_wav_without_opening_device(tmp_path):
    path=tmp_path/"bad.wav"
    path.write_bytes(Recorder._as_wav(bytes(3200),24000)[:-2])
    events,calls=[],[]
    player=SimpleNamespace(player=SimpleNamespace(setSource=lambda *a:pytest.fail("audio device")),
        event=SimpleNamespace(emit=events.append),_play_next=lambda:calls.append("next"))
    assert AudioPlayer._play_now(player,str(path)) is False
    assert calls==[] and events


def test_valid_mono_pcm_preserved():
    raw=bytes([1,0])*400
    wav=Recorder._as_wav(raw,24000)
    validate_pcm_wav(wav)
    assert read_pcm_wav(io.BytesIO(wav))==(24000,1,raw)
