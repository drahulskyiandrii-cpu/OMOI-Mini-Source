"""Voice button contracts, no microphone, playback or inference."""
import json
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller
from omoi_mini.gui import Bridge


def test_voice_button_never_starts_on_stale_stop():
    calls = []
    state = {"work": False, "audio": False}
    fake = SimpleNamespace(recording=False,
        controller=SimpleNamespace(stop=lambda: calls.append("stop"),
                                   voice_busy=lambda: state["work"]),
        window=SimpleNamespace(player=SimpleNamespace(voice_busy=lambda: state["audio"])),
        toggleRecord=lambda: calls.append("toggle"))
    Bridge.voiceAction(fake, "stop")
    Bridge.voiceAction(fake, "finish")
    Bridge.voiceAction(fake, "invalid")
    assert calls == ["stop"]
    Bridge.voiceAction(fake, "start")
    assert calls[-1] == "toggle"
    state["work"] = True
    Bridge.voiceAction(fake, "start")
    state["work"], state["audio"] = False, True
    Bridge.voiceAction(fake, "start")
    assert calls == ["stop", "toggle"]
    fake.recording = True
    Bridge.voiceAction(fake, "start")
    Bridge.voiceAction(fake, "stop")
    Bridge.voiceAction(fake, "finish")
    assert calls == ["stop", "toggle", "toggle"]


def test_voice_jobs_queue_and_stop(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    ctrl = Controller(Store(tmp_path/"mini.sqlite3"))
    events, queued, executed = [], [], []
    ctrl.event.connect(lambda raw: events.append(json.loads(raw)))
    pool = SimpleNamespace(submit=queued.append)
    try:
        turn = ctrl.next_turn()
        ctrl._submit_voice(pool,turn,lambda:executed.append(1))
        ctrl._submit_voice(pool,turn,lambda:executed.append(2))
        assert ctrl.voice_busy()
        queued.pop(0)()
        assert ctrl.voice_busy() and executed == [1]
        ctrl.stop()
        assert not ctrl.voice_busy()
        queued.pop(0)()
        assert executed == [1]
        turn = ctrl.next_turn()
        ctrl._submit_voice(pool,turn,lambda:executed.append(3))
        queued.pop(0)()
        assert not ctrl.voice_busy() and executed == [1,3]
        work = [e for e in events if e["type"]=="voice_work"]
        assert [e["revision"] for e in work] == sorted({e["revision"] for e in work})
        assert work[-1]["busy"] is False
    finally:
        ctrl.close()
