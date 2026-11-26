"""Auto-fit and bounded startup recovery, without model execution."""
import io
from types import SimpleNamespace

import pytest

from omoi_mini import providers


def test_autofit_command_preserves_context_and_reserve():
    command = providers._gemma_command("llama-server.exe", "model.gguf", 18189, 1536)
    for flag, expected in (("--n-gpu-layers", "auto"), ("--fit", "on"),
                           ("--fit-target", "1536"), ("--ctx-size", "2048")):
        assert command[command.index(flag) + 1] == expected
    assert "all" not in command


def test_inherited_llama_options_cannot_override_fit(monkeypatch):
    monkeypatch.setenv("LLAMA_ARG_N_GPU_LAYERS", "all")
    monkeypatch.setenv("LLAMA_ARG_TENSOR_SPLIT", "1")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    env = providers._gemma_environment()
    assert not any(key.upper().startswith("LLAMA_ARG_") for key in env)
    assert env["CUDA_VISIBLE_DEVICES"] == "0"


@pytest.mark.parametrize("messages, expected_attempts, error", [
    ([b"CUDA error: out of memory", b""], 2, None),
    ([b"CUDA error: out of memory", b"failed to allocate CUDA buffer"], 2, "GPU-Speicher"),
    ([b"unknown argument: --fit-target"], 1, "Auto-fit"),
    ([b"port already in use"], 1, "beendet sich"),
])
def test_owned_start_retry_only_on_oom(tmp_path, monkeypatch, messages, expected_attempts, error):
    model, runtime = tmp_path / "test.gguf", tmp_path / "llama-server.exe"
    model.write_bytes(b"GGUF")
    runtime.write_bytes(b"MZ")
    engine = providers.LocalGemma(str(model), str(runtime), strict_owned=True)
    calls, stopped = [], []
    def spawn(command, **kwargs):
        message = messages[len(calls)]
        calls.append(command)
        proc = SimpleNamespace(stderr=io.BytesIO(message), poll=lambda: 1 if message else None,
                               terminate=lambda: stopped.append(True), wait=lambda **kw: 0)
        return proc
    monkeypatch.setattr(providers, "spawn_external", spawn)
    monkeypatch.setattr(engine, "_available", lambda: False)
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **kw: SimpleNamespace(status_code=200))
    try:
        if error:
            with pytest.raises(providers.ProviderFailure, match=error):
                engine.ensure()
            assert engine.process is None
        else:
            engine.ensure()
            assert engine.process is not None
        assert len(calls) == expected_attempts
        assert [c[c.index("--fit-target") + 1] for c in calls] == ["1536", "3072"][:expected_attempts]
    finally:
        engine.close()


def test_oom_marker_across_chunks():
    class Pipe(io.BytesIO):
        def read1(self, size):
            return super().read(7)
    log = providers._GemmaStartupLog(SimpleNamespace(stderr=Pipe(b"CUDA error: out of memory")))
    log.finish()
    assert log.oom.is_set()
    assert not log.unsupported.is_set()

def test_lazy_voice_preparation_precedes_first_model_spawn(tmp_path, monkeypatch):
    model, runtime = tmp_path / "test.gguf", tmp_path / "llama-server.exe"
    model.write_bytes(b"GGUF")
    runtime.write_bytes(b"MZ")
    engine = providers.LocalGemma(str(model), str(runtime), strict_owned=True)
    calls = []
    def spawn(*a, **kw):
        calls.append("LLM")
        return SimpleNamespace(stderr=io.BytesIO(), poll=lambda: None,
                               terminate=lambda: None, wait=lambda **kw: 0)
    monkeypatch.setattr(providers, "spawn_external", spawn)
    monkeypatch.setattr(engine, "_available", lambda: engine.process is not None)
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **kw: SimpleNamespace(status_code=200))
    try:
        prepare = lambda active: calls.extend(["STT", "TTS"])
        engine.ensure(prepare_voice=prepare)
        engine.ensure(prepare_voice=prepare)
        assert calls == ["STT", "TTS", "LLM"]
    finally:
        engine.close()


@pytest.mark.parametrize("stt,tts,speak,ready,expected", [
    ("cuda", "local", True, False, ["STT", "TTS"]),
    ("cuda", "local", True, True, ["TTS"]),
    ("local", "local", True, False, ["TTS"]),
    ("cuda", "cartesia", True, False, ["STT"]),
    ("cuda", "local", False, False, ["STT"]),
    ("cloud", "cartesia", True, False, []),
])
def test_only_selected_gpu_speech_is_prepared(tmp_path, monkeypatch, stt, tts, speak, ready, expected):
    from PySide6.QtCore import QCoreApplication
    from omoi_mini.controller import Controller
    from omoi_mini.core import Store
    QCoreApplication.instance() or QCoreApplication([])
    store = Store(tmp_path / "db")
    store.set_setting("stt", stt)
    store.set_setting("tts", tts)
    store.set_setting("speak", speak)
    store.set_setting("llm_mode", "local_standard")
    ctrl = Controller(store)
    calls = []
    monkeypatch.setattr(ctrl.cuda_whisper, "is_ready", lambda: ready)
    monkeypatch.setattr(ctrl.cuda_whisper, "ensure", lambda **kw: calls.append("STT"))
    monkeypatch.setattr(ctrl.whisper, "ensure", lambda **kw: pytest.fail("CPU STT prewarmed"))
    monkeypatch.setattr(ctrl.local_voice, "prewarm", lambda *a: calls.append("TTS"))
    monkeypatch.setattr(ctrl.gemma, "ensure", lambda *a: pytest.fail("recursive LLM start"))
    try:
        ctrl._prepare_gpu_voice_for_llm(lambda: True)
        assert calls == expected
    finally:
        ctrl.close()

def test_runtime_oom_never_replays_partial_answer(monkeypatch):
    import threading
    engine = providers.LocalGemma()
    stopped = []
    proc = SimpleNamespace(poll=lambda: None, terminate=lambda: stopped.append(True),
                           wait=lambda **kw: 0)
    engine.process = proc
    oom = threading.Event()
    engine._startup_log = SimpleNamespace(oom=oom, finish=lambda: None)
    monkeypatch.setattr(engine, "ensure", lambda *a: None)
    def stream(*a, **kw):
        yield {"choices": [{"delta": {"content": "First sentence."}}]}
        oom.set()
        raise providers.ProviderFailure("synthetic stream error")
    monkeypatch.setattr(providers, "_sse_events", stream)
    try:
        answer = engine.stream_answer([], "de")
        assert next(answer) == "First sentence."
        with pytest.raises(providers.ProviderFailure, match="GPU-Speicher"):
            next(answer)
        assert engine.process is None and stopped == [True]
        assert engine._reserve_floor == 3072
    finally:
        engine.close()
