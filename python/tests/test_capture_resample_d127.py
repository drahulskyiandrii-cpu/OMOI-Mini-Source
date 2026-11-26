"""Rate-Fallback, Anti-Alias und Overflow ohne Mikrofon."""
import io
import sys
import wave
from types import SimpleNamespace
import numpy as np
import pytest
from omoi_mini.pcm_resample import PCMResampler
from omoi_mini.recorder import Recorder


def tone(rate,frequency,seconds=1):
    return np.rint(12000*np.sin(2*np.pi*frequency*np.arange(int(rate*seconds))/rate)).astype(np.int16)


@pytest.mark.parametrize("rate",[16000,32000,44100,48000])
def test_resampler_exact_count_and_chunk_invariance(rate):
    data=tone(rate,997,1.17)
    expected=PCMResampler(rate).feed(data)
    worker=PCMResampler(rate)
    # Auch Einzelsamples an kritischen Phasengrenzen.
    cuts=[1,2,3,4,5,7,11,13,1024,2049,4111,8195,len(data)]
    result=np.concatenate([worker.feed(data[a:b]) for a,b in zip([0]+cuts,cuts)])
    assert len(expected)==len(data)*16000//rate
    assert np.max(np.abs(expected.astype(int)-result.astype(int)))<=1
    if rate==16000:assert np.array_equal(expected,data)


@pytest.mark.parametrize("rate",[32000,44100,48000])
def test_lowpass_retains_speech_band_and_reduces_alias(rate):
    good=PCMResampler(rate).feed(tone(rate,1000))[200:]
    bad=PCMResampler(rate).feed(tone(rate,12000))[200:]
    rms=lambda x:np.sqrt(np.mean(x.astype(float)**2))
    assert .95<rms(good)/(12000/np.sqrt(2))<1.05
    assert rms(bad)/rms(good)<.02


@pytest.mark.parametrize("rate",[32000,44100,48000])
def test_native_rate_fallback_keeps_selected_device_and_output_contract(monkeypatch,rate):
    callbacks=[]
    checks=[]
    def check(**kw):
        checks.append(kw)
        if kw["samplerate"]!=rate:raise OSError("unsupported")
    class Stream:
        def __init__(self,**kw):
            assert kw["samplerate"]==rate and kw["device"]==7
            callbacks.append(kw["callback"])
        def start(self):pass
        def stop(self):pass
        def close(self):pass
    monkeypatch.setitem(sys.modules,"sounddevice",SimpleNamespace(check_input_settings=check,InputStream=Stream))
    monkeypatch.setattr("omoi_mini.recorder.resolve_microphone",lambda *a:7)
    r=Recorder()
    r.configure_endpoint(True)
    r.start("synthetic")
    data=tone(rate,300,1.5).reshape(-1,1)
    for block in np.array_split(data,13):callbacks[0](block,len(block),None,None)
    assert r.signal_level()["seconds"]==1.5
    assert r.snapshot()
    assert not r.endpoint_detected()
    assert all(c["device"]==7 for c in checks)
    with wave.open(io.BytesIO(r.stop()),"rb") as wav:
        assert (wav.getnframes(),wav.getframerate(),wav.getnchannels())==(24000,16000,1)
    assert not r.frames and not r._converted_frames


def test_overflow_discards_corrupt_recording_and_resets_on_restart(monkeypatch):
    callbacks=[]
    class Stream:
        def __init__(self,**kw):callbacks.append(kw["callback"])
        def start(self):pass
        def stop(self):pass
        def close(self):pass
    monkeypatch.setitem(sys.modules,"sounddevice",SimpleNamespace(check_input_settings=lambda **kw:None,InputStream=Stream))
    r=Recorder()
    data=tone(16000,300).reshape(-1,1)
    r.start()
    callbacks[0](data,len(data),None,None)
    assert r.snapshot()
    callbacks[0](data,len(data),None,SimpleNamespace(input_overflow=True))
    assert r.signal_level()["overflow"]
    assert r.snapshot()==b""
    assert r.stop()==b""
    assert r.last_error=="MICROPHONE_INPUT_OVERFLOW"
    r.start()
    callbacks[0](data,len(data),None,SimpleNamespace(input_overflow=True))
    callbacks[1](data,len(data),None,None)
    assert not r.signal_level()["overflow"]
    assert r.stop()


def test_rate_fallback_keeps_thirty_second_limit():
    r=Recorder()
    r._reset_conversion(48000)
    r._accept_frame(np.zeros((48000*31,1),dtype=np.int16))
    assert r._samples==48000*30
    with wave.open(io.BytesIO(r.snapshot()),"rb") as wav:
        assert wav.getnframes()==480000


def test_discard_does_not_resample_or_encode(monkeypatch):
    r=Recorder()
    r._reset_conversion(48000)
    r.stream=SimpleNamespace(stop=lambda:None,close=lambda:None)
    r._accept_frame(tone(48000,300).reshape(-1,1))
    monkeypatch.setattr(r,"_convert_frames",lambda:pytest.fail("discard must not convert"))
    assert r.stop(discard=True)==b""
    assert r.last_metrics["seconds"]==1 and r.last_metrics["captureRate"]==48000
    assert r.stream is None and not r.frames and not r._converted_frames
