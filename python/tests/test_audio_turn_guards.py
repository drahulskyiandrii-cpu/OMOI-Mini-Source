"""Keine veraltete WAV-/PCM-Ausgabe, keine echten Audiogeraete."""
from collections import deque
import json
from types import SimpleNamespace
from PySide6.QtCore import QObject, QCoreApplication, Qt, Slot
from omoi_mini.audio import AudioPlayer
from omoi_mini.controller import Controller
from omoi_mini.core import Store
from omoi_mini.gui import Bridge


def test_player_rejects_old_audio_and_old_stop():
    calls=[]
    fake=SimpleNamespace(_latest_turn=3,_pcm_token=None,_active=False,
        queue=deque(),_streams={},stop=lambda **kw:calls.append("stop"),
        _play_now=lambda path:calls.append(path))
    AudioPlayer.play(fake,"old.wav",2)
    AudioPlayer.enqueue(fake,"old.wav",2)
    AudioPlayer.stop_for_turn(fake,2)
    AudioPlayer.begin_pcm_stream(fake,"old",24000,2)
    assert calls==[] and not fake.queue and not fake._streams
    AudioPlayer.play(fake,"current.wav",3)
    AudioPlayer.enqueue(fake,"next.wav",3)
    assert calls==["stop","current.wav","next.wav"]


def test_queued_wav_is_rejected_after_controller_stop(tmp_path):
    app=QCoreApplication.instance() or QCoreApplication([])
    ctrl=Controller(Store(tmp_path/"db"))
    calls=[]
    class Receiver(QObject):
        @Slot(str,int)
        def deliver(self,path,turn):
            Bridge._play_turn_audio(fake,path,turn)
    fake=SimpleNamespace(recording=False,controller=ctrl,
        window=SimpleNamespace(player=SimpleNamespace(play=lambda *a:calls.append(a))))
    receiver=Receiver()
    ctrl.audio_ready.connect(receiver.deliver,Qt.ConnectionType.QueuedConnection)
    try:
        turn=ctrl.next_turn()
        ctrl.audio_ready.emit("old.wav",turn)
        fresh=ctrl.stop()
        ctrl.audio_ready.emit("new.wav",fresh)
        app.processEvents()
        assert calls==[("new.wav",fresh)]
    finally:
        ctrl.close()


def test_bridge_rejects_stale_enqueue_pcm_and_recording():
    calls=[]
    fake=SimpleNamespace(recording=False,
        controller=SimpleNamespace(current=lambda turn:turn==2),
        window=SimpleNamespace(player=SimpleNamespace(
            enqueue=lambda *a:calls.append(a),
            begin_pcm_stream=lambda *a:calls.append(a),
            play=lambda *a:calls.append(a))))
    Bridge._enqueue_turn_audio(fake,"old.wav",1)
    Bridge._begin_turn_pcm(fake,"old",24000,1)
    fake.recording=True
    Bridge._play_turn_audio(fake,"new.wav",2)
    Bridge._enqueue_turn_audio(fake,"new.wav",2)
    Bridge._begin_turn_pcm(fake,"new",24000,2)
    assert calls==[]
    fake.recording=False
    Bridge._enqueue_turn_audio(fake,"new.wav",2)
    Bridge._begin_turn_pcm(fake,"new",24000,2)
    assert calls==[("new.wav",2),("new",24000,2)]


def test_timer_audio_only_when_completely_idle():
    calls=[]
    state={"work":False,"warm":False,"audio":False}
    fake=SimpleNamespace(recording=False,controller=SimpleNamespace(
        current=lambda turn:turn==3,voice_busy=lambda:state["work"],
        stack_warming=lambda:state["warm"]),window=SimpleNamespace(
        player=SimpleNamespace(voice_busy=lambda:state["audio"],
            play=lambda *args:calls.append(args))))
    for key in state:
        state[key]=True
        Bridge._play_timer_audio(fake,"timer.wav",3)
        state[key]=False
    fake.recording=True
    Bridge._play_timer_audio(fake,"timer.wav",3)
    fake.recording=False
    Bridge._play_timer_audio(fake,"timer.wav",2)
    assert calls==[]
    Bridge._play_timer_audio(fake,"timer.wav",3)
    assert calls==[("timer.wav",3)]


def test_replaced_cancelled_and_closed_timer_cannot_fire(tmp_path,monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    timers=[]
    class FakeTimer:
        def __init__(self,delay,callback):
            self.callback=callback
            timers.append(self)
        def start(self):pass
        def cancel(self):pass
    monkeypatch.setattr("omoi_mini.controller.threading.Timer",FakeTimer)
    ctrl=Controller(Store(tmp_path/"db"))
    events=[]
    ctrl.event.connect(lambda raw:events.append(json.loads(raw)))
    def notices():return [e for e in events if e["type"]=="notice"]
    try:
        ctrl._timer_action("Timer auf eine Sekunde","de")
        ctrl._timer_action("Timer auf zwei Sekunden","de")
        timers[0].callback()
        assert notices()==[]
        timers[1].callback()
        timers[1].callback()
        assert len(notices())==1
        ctrl._timer_action("Timer auf eine Sekunde","de")
        ctrl._timer_action("Stoppe den Timer","de")
        timers[2].callback()
        assert len(notices())==1
        turn=ctrl.next_turn()
        ctrl.stop()
        assert ctrl._timer_action("Timer auf eine Sekunde","de",turn) is None
        assert len(timers)==3
        ctrl._timer_action("Timer auf eine Sekunde","de")
    finally:
        ctrl.close()
    timers[-1].callback()
    assert len(notices())==1
    assert ctrl._timer is None
