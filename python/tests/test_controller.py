"""Durchgängige Controller-Tests ohne Internet, Gebühren oder private Audiodaten."""
import json
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from omoi_mini.core import Store
from omoi_mini.controller import Controller

def wait_for(predicate,timeout=3.0):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        QCoreApplication.processEvents()
        if predicate():return True
        time.sleep(.02)
    return False

def test_introduction_stays_local_and_persists(tmp_path:Path,monkeypatch):
    app=QCoreApplication.instance() or QCoreApplication([])
    db=Store(tmp_path/"dialog.sqlite3")
    db.set_setting("speak",False)
    ctrl=Controller(db)
    events=[]
    ctrl.event.connect(lambda text:events.append(json.loads(text)))
    def no_cloud(*args,**kwargs):
        raise AssertionError("Preset darf kein Cloud-Modell kontaktieren")
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",no_cloud)
    try:
        ctrl.send_text("Kannst du dich vorstellen?")
        assert wait_for(lambda:len(db.messages())==2)
        assert "OMOI Mini" in db.messages()[-1]["content"]
        assert wait_for(lambda:any(e.get("type")=="response" for e in events))
        assert any(e.get("provider")=="preset" for e in events if e.get("type")=="message")
    finally:ctrl.close()

def test_notes_are_deterministic(tmp_path:Path,monkeypatch):
    app=QCoreApplication.instance() or QCoreApplication([])
    db=Store(tmp_path/"notes.sqlite3")
    db.set_setting("speak",False)
    ctrl=Controller(db)
    monkeypatch.setattr("omoi_mini.controller.cloud_answer_stream",lambda *_:(_ for _ in ()).throw(AssertionError("No cloud")))
    try:
        ctrl.send_text("Notiere bitte, dass ich Python lernen muss.")
        assert wait_for(lambda:bool(db.notes()))
        assert db.notes()==["ich Python lernen muss."]
        ctrl.send_text("Was habe ich mir notiert?")
        assert wait_for(lambda:len(db.messages())==4)
        assert "Python" in db.messages()[-1]["content"]
    finally:ctrl.close()

def test_local_tts_preset_uses_local_route_and_cache(tmp_path:Path,monkeypatch):
    import io
    import wave
    app=QCoreApplication.instance() or QCoreApplication([])
    db=Store(tmp_path/"local.sqlite3")
    db.set_setting("tts","local")
    db.set_setting("speak",True)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    calls=[]
    def audio(*args,**kwargs):
        calls.append(args)
        out=io.BytesIO()
        with wave.open(out,"wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\\x00\\x00"*300)
        return out.getvalue()
    monkeypatch.setattr("omoi_mini.controller.local_tts_wav",audio)
    ctrl=Controller(db)
    monkeypatch.setattr(ctrl.local_voice,"ensure",lambda *a,**kw:None)
    try:
        assert ctrl._cache_path("Hallo","de","local","happy") != ctrl._cache_path("Hallo","de","local","neutral")
        assert ctrl._cache_path("Hallo","de","local","happy") != ctrl._cache_path("Hallo","de","cartesia")
        ctrl.startup_greeting()
        assert not calls, "Kein automatischer TTS-Aufruf beim Start"
        ctrl.send_text("Kannst du dich vorstellen?")
        assert wait_for(lambda:bool(calls))
        assert calls[0][1:] == ("de","neutral")
        assert wait_for(lambda:bool(list((tmp_path/"audio-cache").glob("*.wav"))))
    finally:
        ctrl.close()


def test_timer_notification_is_local_and_only_plays_prepared_audio(tmp_path:Path,monkeypatch):
    """Ablauf eines Timers darf niemals automatisch Cartesia aufrufen."""
    from omoi_mini.core import preset_text

    app=QCoreApplication.instance() or QCoreApplication([])
    db=Store(tmp_path/"timer.sqlite3")
    db.set_setting("tts","cartesia")
    db.set_setting("speak",True)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    instances=[]

    class FakeTimer:
        def __init__(self,delay,callback):
            self.delay=delay
            self.callback=callback
            self.daemon=False
            instances.append(self)
        def start(self):pass
        def cancel(self):pass

    monkeypatch.setattr("omoi_mini.controller.threading.Timer",FakeTimer)
    ctrl=Controller(db)
    notices=[]
    audio=[]
    ctrl.event.connect(lambda item:notices.append(json.loads(item)))
    ctrl.timer_audio.connect(lambda path,turn:audio.append(path))
    monkeypatch.setattr(ctrl,"_speak",lambda *args,**kwargs:(_ for _ in ()).throw(
        AssertionError("Timer hat ungefragte Synthese gestartet")))
    try:
        kind,message=ctrl._timer_action("Stell einen Timer auf eine Sekunde.","de")
        assert kind=="timer" and instances[-1].delay==1
        instances[-1].callback()
        assert any(event.get("type")=="notice" for event in notices)
        assert audio==[]

        cache=ctrl._cache_path(preset_text("timer_end","de"),"de","cartesia","neutral")
        cache.parent.mkdir(parents=True,exist_ok=True)
        from omoi_mini.recorder import Recorder
        cache.write_bytes(Recorder._as_wav(bytes(800),24000))
        ctrl._timer_action("Stell einen Timer auf eine Sekunde.","de")
        instances[-1].callback()
        assert audio==[str(cache)]
    finally:
        ctrl.close()
