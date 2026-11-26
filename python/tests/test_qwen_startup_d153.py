"""Qwen startup cleanup, no model processes or network."""
from types import SimpleNamespace
import httpx
import pytest
from omoi_mini import local_tts
from omoi_mini.providers import ProviderFailure


@pytest.fixture
def engine(monkeypatch):
    obj=local_tts.LocalTTSProcess()
    monkeypatch.setattr(local_tts,"_endpoint",lambda:"http://127.0.0.1:18190")
    return obj


@pytest.mark.parametrize("failure",["cancel","timeout","bad_health","exit"])
def test_failed_owned_start_cleans_process(engine,monkeypatch,failure):
    proc=SimpleNamespace(poll=lambda:None,returncode=1)
    engine.process=proc
    stopped=[]
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda p,**kw:stopped.append((p,kw)))
    monkeypatch.setattr(engine,"_ready",lambda *a:False)
    active=[True]
    if failure=="cancel":
        def ready(*a):
            active[0]=False
            return False
        monkeypatch.setattr(engine,"_ready",ready)
    elif failure=="timeout":
        times=iter([0,46])
        monkeypatch.setattr(local_tts.time,"monotonic",lambda:next(times))
    elif failure=="bad_health":
        def bad(*a):
            raise ProviderFailure("invalid health")
        monkeypatch.setattr(engine,"_ready",bad)
    else:
        states=iter([None,1])
        proc.poll=lambda:next(states)
    with pytest.raises(ProviderFailure):
        engine.ensure(lambda:active[0])
    assert engine.process is None
    assert stopped==[(proc,{"tree":True,"timeout":5})]


def test_malformed_health_never_starts_replacement(engine,monkeypatch):
    real=httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kw:real(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,content=b"not json")),**kw))
    monkeypatch.setattr(local_tts,"spawn_external",lambda *a,**k:pytest.fail("spawn"))
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda *a,**k:pytest.fail("stop external"))
    with pytest.raises(ProviderFailure,match="Diagnose"):
        engine.ensure()


def test_external_compatible_service_reused_without_stopping(engine,monkeypatch):
    monkeypatch.setattr(engine,"_ready",lambda *a:True)
    monkeypatch.setattr(local_tts,"spawn_external",lambda *a,**k:pytest.fail("spawn"))
    calls=[]
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda p,**kw:calls.append(p))
    engine.ensure()
    engine.close()
    assert calls==[None]


def test_ready_cancel_keeps_warm_service(engine,monkeypatch):
    proc=SimpleNamespace(poll=lambda:None)
    engine.process=proc
    active=[True]
    def ready(*a):
        active[0]=False
        return True
    monkeypatch.setattr(engine,"_ready",ready)
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda *a,**k:pytest.fail("stop warm"))
    with pytest.raises(ProviderFailure,match="abgebrochen"):
        engine.ensure(lambda:active[0])
    assert engine.process is proc


def test_process_dies_during_health(engine,monkeypatch):
    states=iter([None,1])
    proc=SimpleNamespace(poll=lambda:next(states))
    engine.process=proc
    monkeypatch.setattr(engine,"_ready",lambda *a:True)
    stopped=[]
    monkeypatch.setattr(local_tts,"stop_owned_process",lambda p,**kw:stopped.append(p))
    with pytest.raises(ProviderFailure,match="beendet sich"):
        engine.ensure()
    assert engine.process is None and stopped==[proc]


def test_failed_cleanup_retains_own_handle(engine,monkeypatch):
    proc=SimpleNamespace(poll=lambda:None)
    engine.process=proc
    monkeypatch.setattr(engine,"_ready",lambda *a:False)
    def timeout(*a):
        raise ProviderFailure("timeout")
    monkeypatch.setattr(engine,"_wait_ready",timeout)
    def stop(*a,**kw):
        raise OSError("synthetic stop failed")
    monkeypatch.setattr(local_tts,"stop_owned_process",stop)
    with pytest.raises(OSError):
        engine.ensure()
    assert engine.process is proc


def test_warmup_retries_only_busy(engine,monkeypatch):
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    replies=iter([httpx.Response(429),httpx.Response(200,json={"loaded":True})])
    calls=[]
    def post(*a,**kw):
        calls.append(a[0])
        return next(replies)
    monkeypatch.setattr(local_tts,"cancellable_post",post)
    engine.prewarm()
    assert len(calls)==2


def test_warmup_busy_timeout(engine,monkeypatch):
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    monkeypatch.setattr(local_tts,"cancellable_post",lambda *a,**k:httpx.Response(429))
    times=iter([0,41])
    monkeypatch.setattr(local_tts.time,"monotonic",lambda:next(times))
    with pytest.raises(ProviderFailure,match="40 Sekunden"):
        engine.prewarm()


def test_warmup_busy_cancel_does_not_retry(engine,monkeypatch):
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    calls=[]
    def post(*a,**kw):
        calls.append(True)
        return httpx.Response(429)
    monkeypatch.setattr(local_tts,"cancellable_post",post)
    with pytest.raises(ProviderFailure,match="abgebrochen"):
        engine.prewarm(lambda:not calls)
    assert calls==[True]


def test_warmup_transport_failure_does_not_retry(engine,monkeypatch):
    monkeypatch.setattr(engine,"ensure",lambda *a:None)
    calls=[]
    def post(*a,**kw):
        calls.append(True)
        raise httpx.ReadTimeout("synthetic")
    monkeypatch.setattr(local_tts,"cancellable_post",post)
    with pytest.raises(ProviderFailure):
        engine.prewarm()
    assert calls==[True]
