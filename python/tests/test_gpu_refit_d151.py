"""re-fit only on explicit use after GPU speech selection changes."""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from omoi_mini import providers
from omoi_mini.controller import Controller
from omoi_mini.core import Store


@pytest.fixture
def ctrl(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    c = Controller(Store(tmp_path / "db"))
    try:
        yield c
    finally:
        c.close()


def test_refit_only_on_new_gpu_selection(ctrl, monkeypatch):
    calls = []
    monkeypatch.setattr(ctrl.gemma, "request_refit", lambda: calls.append("refit"))
    ctrl.change_turn_setting("stt", "cuda")
    ctrl.change_turn_setting("stt", "cuda")
    ctrl.change_turn_setting("language", "ru")
    ctrl.change_turn_setting("tts", "local")
    ctrl.change_turn_setting("speak", False)
    ctrl.change_turn_setting("speak", True)
    assert calls == ["refit", "refit", "refit"]


def test_owned_llm_released_once_before_manual_gpu_warmup(ctrl, monkeypatch):
    calls = []
    fake = object()
    ctrl.gemma.process = fake
    monkeypatch.setattr(providers, "stop_owned_process",
                        lambda p: calls.append("release") if p is fake else None)
    monkeypatch.setattr(ctrl.cuda_whisper, "ensure", lambda **kw: calls.append("STT"))
    monkeypatch.setattr(ctrl.local_voice, "prewarm", lambda *a: calls.append("TTS"))
    ctrl.change_turn_setting("stt", "cuda")
    ctrl.change_turn_setting("tts", "local")
    assert calls == []  # configuration alone does not allocate or stop
    tasks = ctrl._stack_tasks(ctrl.store.get_settings())
    for _, task in tasks:
        task()
    assert calls == ["release", "STT", "TTS"]
    assert ctrl.gemma.process is None
    assert not ctrl.gemma._pending_refit()


def test_cancelled_refit_preserves_process_until_next_explicit_request(monkeypatch):
    engine = providers.LocalGemma()
    fake = object()
    stopped = []
    engine.process = fake
    monkeypatch.setattr(providers, "stop_owned_process", lambda p: stopped.append(p))
    engine.request_refit()
    with pytest.raises(providers.ProviderFailure, match="abgebrochen"):
        engine.release_for_refit(lambda: False)
    assert engine.process is fake and stopped == []
    assert engine._pending_refit()
    engine.release_for_refit()
    engine.release_for_refit()
    assert stopped == [fake]
    assert engine.process is None and not engine._pending_refit()


def test_refit_requested_during_release_is_not_lost(monkeypatch):
    engine = providers.LocalGemma()
    engine.process = object()
    engine.request_refit()
    calls = []
    def release(p):
        calls.append(p)
        if len(calls) == 1:
            engine.request_refit()
    monkeypatch.setattr(providers, "stop_owned_process", release)
    engine.release_for_refit()
    assert engine._pending_refit()
    engine.release_for_refit()
    assert not engine._pending_refit()
    assert calls[1] is None


def test_cold_llm_preparation_never_reenters_refit_lock(ctrl, monkeypatch):
    ctrl.store.set_setting("stt", "cuda")
    ctrl.store.set_setting("tts", "local")
    monkeypatch.setattr(ctrl.gemma, "release_for_refit", lambda *a: pytest.fail("recursive lock"))
    monkeypatch.setattr(ctrl.cuda_whisper, "ensure", lambda **kw: None)
    monkeypatch.setattr(ctrl.local_voice, "prewarm", lambda *a: None)
    with ctrl.gemma.lock:
        ctrl._prepare_gpu_voice_for_llm(lambda: True)


def test_old_log_marker_is_not_repeated_after_clear():
    first = threading.Event()
    resume = threading.Event()
    class Pipe:
        count = 0
        def read(self, size):
            self.count += 1
            if self.count == 1:
                return b"CUDA error: out of memory"
            if self.count == 2:
                first.set()
                assert resume.wait(2)
                return b"ordinary later message"
            return b""
        def close(self):
            pass
    log = providers._GemmaStartupLog(SimpleNamespace(stderr=Pipe()))
    try:
        assert first.wait(2)
        assert log.oom.is_set()
        log.oom.clear()
    finally:
        resume.set()
        log.finish()
    assert not log.oom.is_set()


def test_process_death_during_health_cannot_report_ready(monkeypatch):
    engine = providers.LocalGemma()
    states = iter([None, 1])
    engine.process = SimpleNamespace(poll=lambda: next(states))
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **kw: SimpleNamespace(status_code=200))
    with pytest.raises(providers.ProviderFailure, match="beendet sich"):
        engine._wait_ready(lambda: True)
