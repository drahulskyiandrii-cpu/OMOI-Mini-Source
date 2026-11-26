"""Qt-Desktop-Fenster und begrenzte QWebChannel-Schnittstelle."""
from __future__ import annotations
import json
import os
import sqlite3
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Slot, Qt, QUrl, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QCursor
from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from .core import Store,KNOWN_SLOTS,ALTERNATIVE_ANIMATION_SLOTS,resolve_animation,user_data_dir
from .animation_assets import import_vrma, validate_vrma, InvalidAnimation
from .avatar_catalog import installed_avatars, InstalledAvatar, _metadata_name
from .asset_library import import_vrm, InvalidAvatar, save_selected_animation_defaults
from .voice_assets import import_voice_wav, InvalidVoice
from .web_server import WebAssets
from .controller import Controller
from .recorder import Recorder, MicrophoneUnavailable, microphone_choices
from .audio import AudioPlayer
from .runtime_config import (RUNTIME_SETTINGS, PYTHON_SETTINGS, runtime_overrides,
                             runtime_environment, runtime_path_sources)
from .runtime_diagnostics import inspect_local_runtimes
from .model_inventory import installation_rows
from .window_layout import Rect, dock_geometry

# Anbieter- und Sprachparameter gelten fuer den gesamten Turn.
TURN_SETTINGS=frozenset({
    "llm_mode","llm","llm_local_profile","stt","tts","speak","language",
    "qwen_variant","piper_voice_de","piper_voice_ru","stt_profile",
    "stt_hotwords","stt_auto_language","stt_speech_guard","stt_auto_endpoint","stt_review",
})
RECORDING_SETTINGS_ERROR="Bitte Aufnahme vor dem Wechsel der Spracheinstellungen beenden."

class Bridge(QObject):
    uiEvent=Signal(str)
    def __init__(self,window:"MiniWindow"):
        super().__init__(window)
        self.window=window
        self.controller=window.controller
        self.recorder=Recorder()
        self.recording=False
        self._recording_turn=-1
        self._recording_started=0.0
        self._stt_partial_timer=QTimer(self)
        self._stt_partial_timer.setInterval(1200)
        self._stt_partial_timer.timeout.connect(self._recording_partial)
        self._mic_level_timer=QTimer(self)
        self._mic_level_timer.setInterval(250)
        self._mic_level_timer.timeout.connect(self._recording_level)
        self.pending_names:dict[str,str]={}
        self.catalog=installed_avatars()
        self._refresh_avatar_catalog()
        self.controller.event.connect(self._forward_controller_event)
        self.controller.audio_ready.connect(self._play_turn_audio)
        self.controller.audio_enqueue.connect(self._enqueue_turn_audio)
        self.controller.timer_audio.connect(self._play_timer_audio)
        self.controller.stop_audio.connect(window.player.stop_for_turn)
        self.controller.audio_stream_start.connect(self._begin_turn_pcm)
        self.controller.audio_stream_chunk.connect(window.player.pcm_chunk)
        self.controller.audio_stream_finish.connect(window.player.pcm_finished)
        self.controller.audio_stream_abort.connect(window.player.pcm_abort)
        window.player.event.connect(self.uiEvent.emit)
    @Slot(str)
    def _forward_controller_event(self,raw):
        # Qt stellt Worker-Signale zeitversetzt zu. Erst hier ist sicher,
        # ob der Turn noch zur aktuellen Oberflaeche gehoert.
        event=json.loads(raw)
        turn=event.get("turn")
        if turn is None or self.controller.current(turn):
            self.uiEvent.emit(raw)

    @Slot(str,int)
    def _play_turn_audio(self,path,turn):
        if not self.recording and self.controller.current(turn):
            self.window.player.play(path,turn)

    @Slot(str,int)
    def _enqueue_turn_audio(self,path,turn):
        if not self.recording and self.controller.current(turn):
            self.window.player.enqueue(path,turn)

    @Slot(str,int,int)
    def _begin_turn_pcm(self,token,rate,turn):
        if not self.recording and self.controller.current(turn):
            self.window.player.begin_pcm_stream(token,rate,turn)

    @Slot(str,int)
    def _play_timer_audio(self,path,turn):
        # Eine Timer-Meldung darf weder Aufnahme noch Antwort unterbrechen.
        if (not self.recording and self.controller.current(turn)
                and not self.controller.voice_busy() and not self.controller.stack_warming()
                and not self.window.player.voice_busy()):
            self.window.player.play(path,turn)

    def _refresh_avatar_catalog(self):
        """Auch bewusst gewählte externe VRM-Dateien ohne Kopieren anzeigen."""
        self.catalog=installed_avatars()
        seen={item.path for item in self.catalog}
        extra=[*self.window.store.favorite_avatars()]
        if self.window.assets.avatar:
            extra.append(str(self.window.assets.avatar))
        for raw in extra:
            path=Path(raw)
            try:
                if (path.suffix.lower()==".vrm" and path.is_file()
                        and not path.is_symlink()
                        and 100<=path.stat().st_size<200*1024*1024
                        and path.resolve() not in seen):
                    path=path.resolve()
                    self.catalog.append(InstalledAvatar(
                        path,(_metadata_name(path) or "Eigenes VRM") + " · eigene Datei"
                    ))
                    seen.add(path)
            except OSError:
                continue

    def _avatar_listing(self) -> list[dict[str,str|bool]]:
        self._refresh_avatar_catalog()
        approved=set(self.window.store.favorite_avatars())
        return [{"id":str(i),"name":item.label,
                 "favorite":str(item.path) in approved}
                for i,item in enumerate(self.catalog)]

    def _avatar_index(self) -> str:
        return next((str(i) for i,item in enumerate(self.catalog)
                     if item.path==self.window.assets.avatar),"")

    @Slot()
    def toggleAvatarFavorite(self):
        """Nur Mini-SQLite: Original-Bibliothek und große OMOI bleiben unverändert."""
        selected=self.window.assets.avatar
        if selected is None:
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Bitte zuerst ein VRM zur Ansicht auswählen."}))
            return
        try:
            approved=set(self.window.store.favorite_avatars())
            self.window.store.set_avatar_favorite(
                str(selected),str(selected) not in approved
            )
        except (ValueError,OSError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Mini-Favorit konnte nicht gespeichert werden."}))
            return
        self.uiEvent.emit(json.dumps({
            "type":"avatar_favorites",
            "avatars":self._avatar_listing(),
            "selectedAvatar":self._avatar_index(),
            "favoriteCount":len(self.window.store.favorite_avatars()),
        },ensure_ascii=False))

    @Slot()
    def ready(self):
        public_settings=self.window.store.get_settings()
        from .providers import cartesia_voice_status
        voice_id=public_settings.pop("cartesia_voice_id","")
        public_settings["cartesia_voice_status"]=cartesia_voice_status(voice_id)
        public_settings.pop("qwen_voice_reference",None)
        public_settings.pop("llm_model_file",None)
        public_settings.pop("llm_gemma12_file",None)
        public_settings.pop("llm_gemma12_server_file",None)
        public_settings.pop("llm_server_file",None)
        runtime_sources=runtime_path_sources(public_settings)
        for key in RUNTIME_SETTINGS:
            public_settings.pop(key,None)
        self.uiEvent.emit(json.dumps({"type":"init","settings":public_settings,
          "runtimeSources":runtime_sources,
          "llmStatus":Bridge._llm_status(self),
          "messages":self.window.store.messages(35),
          "slots":list(KNOWN_SLOTS.keys()),"availableAlternatives":Bridge._available_alternatives(self),"availablePrimary":Bridge._available_primary(self),"hasAvatar":self.window.assets.avatar is not None,
          "avatars":self._avatar_listing(),
          "selectedAvatar":self._avatar_index(),
          "favoriteCount":len(self.window.store.favorite_avatars()),
          "animations":{slot:"Benutzerdefiniert" for slot in self.window.store.animations(
              str(self.window.assets.avatar or ""))},
          "otherAvatarAssignments":self.window.store.other_avatar_animation_count(
              str(self.window.assets.avatar or ""))},ensure_ascii=False))
        settings=self.window.store.get_settings()
        # Kein Modell- oder GPU-Start allein durch Öffnen des Fensters.
        # Der alte SQLite-Autostart-Schalter bleibt nur zur Datenkompatibilitaet.
        QTimer.singleShot(700,self.controller.startup_greeting)
    @Slot(str)
    def sendText(self,text:str):
        if self.recording:
            self.uiEvent.emit(json.dumps({"type":"error","value":RECORDING_SETTINGS_ERROR}))
            return
        self.controller.send_text(text)
    @Slot(int,str,bool)
    def submitSTTReview(self,turn:int,text:str,remember:bool):
        try:
            if not self.controller.submit_stt_review(turn,text,remember):
                raise ValueError("STT review unavailable")
        except (ValueError,sqlite3.Error,OSError):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Transkript nicht gesendet. Text oder Aufnahme pr\u00fcfen."}))
    @Slot(int)
    def cancelSTTReview(self,turn:int):
        self.controller.cancel_stt_review(turn)
    @Slot()
    def clearSTTCorrections(self):
        try:
            self.window.store.clear_stt_corrections()
            self.uiEvent.emit(json.dumps({"type":"notice","value":"Pers\u00f6nliche STT-Korrekturen gel\u00f6scht."}))
        except sqlite3.Error:
            self.uiEvent.emit(json.dumps({"type":"error","value":"STT-Korrekturen konnten nicht gel\u00f6scht werden."}))
    def _recording_config_error(self):
        self.close_recording()
        self.controller.stop()
        self.uiEvent.emit(json.dumps({"type":"error","value":"Aufnahme abgebrochen. Einstellungen nicht lesbar."}))

    def _recording_level(self):
        if not self.recording:
            return
        level=self.recorder.signal_level()
        self.uiEvent.emit(json.dumps({
            "type":"mic_level",**level,
        },ensure_ascii=False))
        if level.get("overflow"):
            self.toggleRecord()
            return
        if level["seconds"]>=29.98 or time.monotonic()-self._recording_started>=30:
            self.uiEvent.emit(json.dumps({"type":"mic_limit",
                "value":"Aufnahme-Limit von 30 Sekunden erreicht; der aufgenommene Teil wird transkribiert."},ensure_ascii=False))
            self.toggleRecord()
        else:
            try:settings=self.window.store.get_settings()
            except (ValueError,OSError,sqlite3.Error):
                Bridge._recording_config_error(self)
                return
            if (settings.get("stt_auto_endpoint") and settings.get("stt_profile")!="fast"
                    and settings["stt"] in ("cuda","local") and self.recorder.endpoint_detected()):
                self.toggleRecord()

    def close_recording(self):
        """Mikrofon auch beim Schliessen des Fensters freigeben."""
        self._stt_partial_timer.stop()
        self._mic_level_timer.stop()
        if self.recording:
            self.recording=False
            try:
                self.recorder.stop(discard=True)
            except Exception:
                pass
            self.uiEvent.emit(json.dumps({"type":"recording","active":False}))

    def _recording_partial(self):
        if not self.recording or not self.controller.current(self._recording_turn):
            return
        try:
            if self.window.store.get_settings()["stt"]!="cuda":return
            if not self.controller.partial_available(self._recording_turn):return
            snapshot=self.recorder.snapshot()
            if snapshot:self.controller.partial_audio(snapshot,self._recording_turn)
        except (ValueError,OSError,sqlite3.Error):
            Bridge._recording_config_error(self)

    @Slot()
    def refreshMicrophones(self):
        selected=""
        try:
            selected=self.window.store.get_settings()["microphone_id"]
            devices=microphone_choices()
        except Exception:
            self.uiEvent.emit(json.dumps({"type":"microphones","devices":[],
                "selected":selected,"error":"Mikrofonliste konnte nicht gelesen werden."}))
            return
        self.uiEvent.emit(json.dumps({"type":"microphones","devices":devices,
            "selected":selected},ensure_ascii=False))

    @Slot(str)
    def chooseMicrophone(self, selection:str):
        if self.recording:
            self.uiEvent.emit(json.dumps({"type":"error","value":"Bitte Aufnahme vor dem Mikrofonwechsel beenden."}))
            self.refreshMicrophones()
            return
        try:
            if selection!="default" and not any(row["id"]==selection and row["available"]
                                                for row in microphone_choices()):
                raise ValueError("Mikrofon fehlt")
            self.window.store.set_setting("microphone_id",selection)
        except Exception:
            self.uiEvent.emit(json.dumps({"type":"error","value":"Mikrofon nicht verfügbar oder Auswahl nicht speicherbar."}))
        self.refreshMicrophones()

    @Slot(str)
    def voiceAction(self,action:str):
        # The requested action is explicit: a stale Stop click must not open the mic.
        if action=="finish":
            if self.recording:
                self.toggleRecord()
        elif action=="stop":
            if not self.recording:
                self.controller.stop()
        elif action=="start":
            if not self.recording and not self.controller.voice_busy() and not self.window.player.voice_busy():
                self.toggleRecord()

    @Slot()
    def toggleRecord(self):
        if self.controller.stack_warming():return
        if self.recording:
            self.recording=False
            self._stt_partial_timer.stop()
            self._mic_level_timer.stop()
            try:audio=self.recorder.stop()
            except Exception:
                self.controller.stop()
                self.uiEvent.emit(json.dumps({"type":"recording","active":False}))
                self.uiEvent.emit(json.dumps({"type":"error","value":"Aufnahme konnte nicht beendet werden."}))
                return
            self.uiEvent.emit(json.dumps({"type":"recording","active":False}))
            metrics=self.recorder.last_metrics
            self.uiEvent.emit(json.dumps({
                "type":"mic_captured",
                "seconds":metrics.get("seconds",0),
                "rms":metrics.get("rms",0),
                "voiceMs":metrics.get("voiceMs",0),
                "captureRate":metrics.get("captureRate",16000),
                "overflow":metrics.get("overflow",False),
                "accepted":bool(audio),
            }))
            if not audio:
                self.controller.stop()
                problem=self.recorder.last_error
                message=(
                    "Mikrofonsignal zu leise. Mikrofon und Eingangslautstärke prüfen."
                    if problem=="MICROPHONE_SIGNAL_TOO_QUIET"
                    else "Aufnahme zu kurz oder kein Audiosignal empfangen."
                )
                if problem=="MICROPHONE_INPUT_OVERFLOW":
                    message="Mikrofon-Daten verloren. Bitte Aufnahme wiederholen."
                self.uiEvent.emit(json.dumps({"type":"error","value":message}))
                return
            # Ein zuvor abgebrochener Aufnahme-Turn darf nicht neu starten.
            if not self.controller.current(self._recording_turn):return
            self.controller.listen_audio(audio,self._recording_turn)
        else:
            turn=self.controller.stop()
            try:settings=self.window.store.get_settings()
            except (ValueError,OSError,sqlite3.Error):
                self.uiEvent.emit(json.dumps({"type":"error","value":"Aufnahme abgebrochen. Einstellungen nicht lesbar."}))
                return
            self.recorder.configure_endpoint(settings.get("stt_auto_endpoint",False)
                and settings.get("stt_profile","fast")!="fast" and settings["stt"] in ("cuda","local"))
            try:self.recorder.start(settings["microphone_id"])
            except MicrophoneUnavailable as exc:
                self.uiEvent.emit(json.dumps({"type":"error","value":str(exc)},ensure_ascii=False))
                return
            except Exception:
                self.uiEvent.emit(json.dumps({"type":"error","value":"Kein Mikrofon verfügbar oder Zugriff verweigert."}))
                return
            self.recording=True
            self._recording_started=time.monotonic()
            self._mic_level_timer.start()
            self._recording_turn=turn
            if settings["stt"]=="cuda":
                self._stt_partial_timer.start()
            self.uiEvent.emit(json.dumps({"type":"recording","active":True}))
            self.uiEvent.emit(json.dumps({"type":"status","value":"Hört zu…","avatarState":"listening"}))
    def _llm_status(self):
        settings=self.window.store.get_settings()
        def present(key, suffix):
            raw=settings.get(key,"")
            if not raw:return False
            try:
                path=Path(raw)
                return (path.is_absolute() and path.is_file() and not path.is_symlink() and path.suffix.lower()==suffix and (suffix!=".exe" or path.name.lower()=="llama-server.exe"))
            except (OSError,ValueError):return False
        active=getattr(self.controller,"active_local_profile",settings["llm_local_profile"])
        gemma=getattr(self.controller,"gemma",None)
        model_key="llm_gemma12_file" if active=="gemma12" else "llm_model_file"
        server=settings["llm_gemma12_server_file"] or settings["llm_server_file"] if active=="gemma12" else settings["llm_server_file"]
        changed=gemma is not None and (gemma.model_file!=settings[model_key] or gemma.server_file!=server)
        return {"activeProfile":active,"selected":settings["llm_mode"],
                "standardModel":present("llm_model_file",".gguf"),
                "standardRuntime":present("llm_server_file",".exe"),
                "largeModel":present("llm_gemma12_file",".gguf"),
                "largeRuntime":present("llm_gemma12_server_file",".exe") or (not settings["llm_gemma12_server_file"] and present("llm_server_file",".exe")),
                "restartRequired":settings["llm"]=="local" and (active!=settings["llm_local_profile"] or changed)}

    @Slot()
    def prewarmVoiceStack(self):
        if self.recording:return
        self.controller.prewarm_stack()

    @Slot()
    def cancelWarmup(self):self.controller.cancel_stack_warmup()

    @Slot()
    def readLLMStatus(self):
        try:report=self._llm_status()
        except (OSError,ValueError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Laufzeitdiagnose konnte nicht abgeschlossen werden."}))
            return
        self.uiEvent.emit(json.dumps({"type":"llm_status","report":report}))

    @Slot(str,str)
    def saveSetting(self,key:str,value:str):
        try:
            if key in RUNTIME_SETTINGS or key in ("llm_model_file","llm_server_file","llm_gemma12_file","llm_gemma12_server_file","cartesia_voice_id","microphone_id","qwen_voice_reference"):
                raise ValueError("Native Pfadauswahl erforderlich")
            parsed=json.loads(value)
            previous=self.window.store.get_settings().get(key)
            if key in TURN_SETTINGS:
                if self.recording and previous!=parsed:
                    self.uiEvent.emit(json.dumps({"type":"setting","key":key,"value":previous}))
                    self.uiEvent.emit(json.dumps({"type":"error","value":RECORDING_SETTINGS_ERROR}))
                    return
                self.controller.change_turn_setting(key,parsed)
            else:
                self.window.store.set_setting(key,parsed)
            if key=="always_on_top":self.window.set_always_on_top(bool(parsed))
            # Die Anbieterwahl selbst darf keine Modelle laden.
            self.uiEvent.emit(json.dumps({"type":"setting","key":key,"value":parsed},ensure_ascii=False))
            if key in ("llm_mode","llm","llm_local_profile"):self.readLLMStatus()
        except (ValueError,TypeError,OSError,RuntimeError,sqlite3.Error):
            # Bei einem fehlgeschlagenen Save den sichtbaren Schalter auf
            # den gespeicherten Wert zuruecksetzen, sofern lesbar.
            if key in TURN_SETTINGS or key in ("ui_language","greeting","always_on_top","mouse_tracking"):
                try:
                    stored=self.window.store.get_settings()[key]
                    self.uiEvent.emit(json.dumps({"type":"setting","key":key,"value":stored}))
                except (ValueError,KeyError,OSError,sqlite3.Error):pass
            self.uiEvent.emit(json.dumps({"type":"error","value":"Einstellung ungültig oder nicht speicherbar"}))
    @Slot(str)
    def saveCartesiaVoice(self, value:str):
        from .providers import cartesia_voice_status
        try:
            voice=value.strip()
            before=self.window.store.get_settings()["cartesia_voice_id"]
            if self.recording and before!=voice:
                self.uiEvent.emit(json.dumps({"type":"error","value":RECORDING_SETTINGS_ERROR}))
                return
            if self.window.store.get_settings()["tts"]=="cartesia":
                self.controller.change_turn_setting("cartesia_voice_id",voice)
            else:
                self.window.store.set_setting("cartesia_voice_id",voice)
        except (ValueError,TypeError,OSError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Cartesia Voice-ID ungültig oder nicht speicherbar."}))
            return
        self.uiEvent.emit(json.dumps({"type":"cartesia_voice_status","value":cartesia_voice_status(voice)}))

    def _native_change_allowed(self):
        if self.recording:
            self.uiEvent.emit(json.dumps({"type":"error","value":RECORDING_SETTINGS_ERROR}))
            return False
        return True

    def _save_native_setting(self,key,value):
        # Dateidialoge koennen Qt-Ereignisse ausloesen. Vor dem Speichern
        # nochmals pruefen und den alten Turn atomar ungueltig machen.
        if not Bridge._native_change_allowed(self):return False
        try:
            self.controller.change_turn_setting(key,value)
        except (OSError,ValueError,RuntimeError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Einstellung ungültig oder nicht speicherbar"}))
            return False
        return True

    @Slot(str)
    def chooseRuntimePath(self,key:str):
        """Nur native Dateiauswahl; keine Ausführung ausgewählter Dateien."""
        if not Bridge._native_change_allowed(self):return
        if key not in RUNTIME_SETTINGS:
            return
        try:
            saved=self.window.store.get_settings()
            start=saved.get(key) or os.environ.get(RUNTIME_SETTINGS[key]) or str(Path.home())
            if key in PYTHON_SETTINGS:
                filename,_=QFileDialog.getOpenFileName(
                    self.window,"Python-Interpreter auswählen",start,"Python (python.exe)")
            else:
                filename=QFileDialog.getExistingDirectory(
                    self.window,"Lokales Komponentenverzeichnis auswählen",start)
            if not filename:
                return
            path=Path(filename)
            valid=(path.is_file() and path.name.lower()=="python.exe"
                   if key in PYTHON_SETTINGS else path.is_dir())
            if not valid:
                raise ValueError("Ungültiger Pfad")
            if not Bridge._save_native_setting(self,key,str(path.resolve())):return
        except (OSError,ValueError,RuntimeError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Laufzeitpfad ungültig oder nicht speicherbar."}))
            return
        Bridge._emit_runtime_paths(self)

    def _emit_runtime_paths(self):
        try:sources=runtime_path_sources(self.window.store.get_settings())
        except (OSError,ValueError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Laufzeitdiagnose konnte nicht abgeschlossen werden."}))
            return
        self.uiEvent.emit(json.dumps({"type":"runtime_paths","sources":sources}))

    @Slot(str)
    def resetRuntimePath(self,key:str):
        if not Bridge._native_change_allowed(self) or key not in RUNTIME_SETTINGS:return
        if Bridge._save_native_setting(self,key,""):Bridge._emit_runtime_paths(self)

    @Slot()
    def readModelCatalog(self):
        """Keine Netzwerkverbindung; nur statische öffentliche Metadaten."""
        try:
            rows=installation_rows(self.window.store.path.parent,self.window.store.get_settings())
        except (OSError,ValueError,RuntimeError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Modelldiagnose fehlgeschlagen."}))
            return
        self.uiEvent.emit(json.dumps({"type":"model_catalog","models":rows}))

    @Slot()
    def diagnoseRuntimePaths(self):
        """Diagnose der gespeicherten (beim Neustart wirksamen) Pfade."""
        try:
            env=runtime_environment(runtime_overrides(self.window.store.get_settings()))
            report=inspect_local_runtimes(env=env)
        except (OSError,RuntimeError,ValueError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Laufzeitdiagnose konnte nicht abgeschlossen werden."}))
            return
        self.uiEvent.emit(json.dumps({"type":"runtime_diagnosis","report":report}))

    @Slot()
    def openVRoidHub(self):
        QDesktopServices.openUrl(QUrl("https://hub.vroid.com/"))

    @Slot()
    def selectVoiceReference(self):
        """Persönliche WAV für späteres Klonen speichern; keine GPU-Inferenz."""
        if not Bridge._native_change_allowed(self):return
        filename,_=QFileDialog.getOpenFileName(
            self.window, "Eigene oder genehmigte Referenzstimme auswählen",
            str(Path.home()), "WAV-Dateien (*.wav)"
        )
        if not filename or not Bridge._native_change_allowed(self):return
        try:
            saved=import_voice_wav(filename)
            if not Bridge._save_native_setting(self,"qwen_voice_reference",str(saved)):return
        except (InvalidVoice,OSError,ValueError):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Stimmprobe ungültig. Mono PCM16 WAV, 3–30 Sekunden erforderlich."}))
            return
        self.uiEvent.emit(json.dumps({"type":"notice",
            "value":"Private Stimmprobe in Mini gespeichert. Beim nächsten Programmstart wird sie für Qwen genutzt; keine GPU-Inferenz gestartet."},ensure_ascii=False))

    @Slot()
    def chooseLocalGGUF(self):
        if not Bridge._native_change_allowed(self):return
        filename,_=QFileDialog.getOpenFileName(
            self.window,"Eigenes GGUF-Sprachmodell auswählen",str(Path.home()),"GGUF (*.gguf)")
        if not filename:return
        path=Path(filename)
        if path.suffix.lower()!=".gguf" or not path.is_file() or path.is_symlink():
            self.uiEvent.emit(json.dumps({"type":"error","value":"Ungültige GGUF-Datei."}))
            return
        if not Bridge._save_native_setting(self,"llm_model_file",str(path.resolve())):return
        self.uiEvent.emit(json.dumps({"type":"notice",
            "value":"GGUF-Modell gewählt; es wird erst beim nächsten Start der App geladen."}))

    @Slot()
    def chooseGemma12GGUF(self):
        if not Bridge._native_change_allowed(self):return
        filename,_=QFileDialog.getOpenFileName(
            self.window,"Gemma 4 12B GGUF auswählen",str(Path.home()),"GGUF (*.gguf)")
        if not filename:return
        path=Path(filename)
        if path.suffix.lower()!=".gguf" or not path.is_file() or path.is_symlink():
            self.uiEvent.emit(json.dumps({"type":"error","value":"Ungültige GGUF-Datei."}))
            return
        if not Bridge._save_native_setting(self,"llm_gemma12_file",str(path.resolve())):return
        self.uiEvent.emit(json.dumps({"type":"notice",
            "value":"Separates 12B-Modell gespeichert. Nach Neustart aktiv, wenn das 12B-Profil gewählt ist."}))

    @Slot()
    def chooseGemma12Server(self):
        if not Bridge._native_change_allowed(self):return
        filename,_=QFileDialog.getOpenFileName(
            self.window,"llama-server.exe für Gemma 12B",str(Path.home()),"llama-server.exe (llama-server.exe)")
        if not filename:return
        path=Path(filename)
        if path.name.lower()!="llama-server.exe" or not path.is_file() or path.is_symlink():
            self.uiEvent.emit(json.dumps({"type":"error","value":"Ungültige llama-server.exe."}))
            return
        if not Bridge._save_native_setting(self,"llm_gemma12_server_file",str(path.resolve())):return
        self.uiEvent.emit(json.dumps({"type":"notice","value":"12B-Laufzeit gespeichert; Neustart erforderlich."}))

    @Slot()
    def chooseLlamaServer(self):
        if not Bridge._native_change_allowed(self):return
        filename,_=QFileDialog.getOpenFileName(
            self.window,"llama-server.exe auswählen",str(Path.home()),"llama-server.exe (llama-server.exe)")
        if not filename:return
        path=Path(filename)
        if path.name.lower()!="llama-server.exe" or not path.is_file() or path.is_symlink():
            self.uiEvent.emit(json.dumps({"type":"error","value":"Bitte llama-server.exe auswählen."}))
            return
        if not Bridge._save_native_setting(self,"llm_server_file",str(path.resolve())):return
        self.uiEvent.emit(json.dumps({"type":"notice",
            "value":"llama.cpp-Laufzeit gewählt; sie wird erst beim nächsten Programmstart verwendet."}))

    @Slot()
    def useSelectedAnimationsAsDefaults(self):
        current=self.window.assets.avatar
        if current is None:
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Bitte zuerst einen Avatar mit gespeicherten Animationen auswählen."}))
            return
        try:
            chosen=save_selected_animation_defaults(self.window.store,str(current))
        except (ValueError,OSError):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Keine gültigen gespeicherten Animationen vorhanden."}))
            return
        self.uiEvent.emit(json.dumps({"type":"notice",
            "value":f"{len(chosen)} Animationen als private Mini-Standards gespeichert. Für öffentliche Weitergabe sind separate Lizenzen nötig."},ensure_ascii=False))

    def _apply_avatar(self,path:Path):
        if (not path.is_file() or path.suffix.lower()!=".vrm"
                or not 100 <= path.stat().st_size < 200*1024*1024):
            raise ValueError("Ungültige VRM-Datei")
        self.window.store.set_setting("avatar",str(path))
        self.window.assets.staged_animations.clear()
        self.pending_names.clear()
        self.window.assets.set_avatar(path)
        assignments={slot:"Benutzerdefiniert" for slot in self.window.store.animations(str(path))}
        self._refresh_avatar_catalog()
        chosen=self._avatar_index()
        self.uiEvent.emit(json.dumps({"type":"avatar","available":True,
            "selectedAvatar":chosen,"animations":assignments,
            "availableAlternatives":Bridge._available_alternatives(self),"availablePrimary":Bridge._available_primary(self),
            "otherAvatarAssignments":self.window.store.other_avatar_animation_count(str(path)),
            "avatars":self._avatar_listing(),"favoriteCount":len(self.window.store.favorite_avatars())},
            ensure_ascii=False))
    @Slot()
    def selectAvatar(self):
        filename,_=QFileDialog.getOpenFileName(self.window,"VRM-Modell auswählen",str(Path.home()),"VRM-Modelle (*.vrm)")
        if not filename:return
        try:
            self._apply_avatar(import_vrm(Path(filename).resolve()))
        except (InvalidAvatar,ValueError,OSError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Ungültige VRM-Datei"}))
    @Slot(str)
    def chooseInstalledAvatar(self,index:str):
        try:
            choice=int(index)
            if not 0<=choice<len(self.catalog):raise ValueError()
            self._apply_avatar(self.catalog[choice].path)
        except (ValueError,OSError,sqlite3.Error):
            self.uiEvent.emit(json.dumps({"type":"error","value":"Dieses VRM-Modell ist nicht verfügbar."}))
    def _available_primary(self):
        avatar=str(self.window.assets.avatar or "")
        if not avatar:return []
        return [slot for slot in KNOWN_SLOTS if slot not in ALTERNATIVE_ANIMATION_SLOTS
                and resolve_animation(slot,avatar,self.window.store) is not None]

    def _available_alternatives(self):
        avatar=str(self.window.assets.avatar or "")
        if not avatar:return []
        return [slot for slot in ALTERNATIVE_ANIMATION_SLOTS
                if resolve_animation(slot,avatar,self.window.store) is not None]

    @Slot(str)
    def selectAnimation(self,slot:str):
        if slot not in KNOWN_SLOTS:return
        if not self.window.assets.avatar:
            self.uiEvent.emit(json.dumps({"type":"error","value":"Bitte zuerst einen Avatar auswählen."}))
            return
        filename,_=QFileDialog.getOpenFileName(self.window,"VRMA auswählen",str(Path.home()),"VRM Animation (*.vrma)")
        if not filename:return
        try:
            imported=import_vrma(filename,user_data_dir()/"animations")
            self.window.assets.staged_animations[slot]=imported.path
            self.pending_names[slot]=imported.original_name
            self.uiEvent.emit(json.dumps({"type":"animation_pending","slot":slot,
                "filename":imported.original_name},ensure_ascii=False))
        except (InvalidAnimation,OSError):
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Keine verwendbare VRMA-Datei. Die bisherige Animation bleibt aktiv."}))
    @Slot(str)
    def confirmAnimation(self,slot:str):
        selected=self.window.assets.staged_animations.get(slot)
        if slot not in KNOWN_SLOTS or not selected or not self.window.assets.avatar:
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Keine geprüfte Animation für den ausgewählten Avatar vorhanden."}))
            return
        try:
            validate_vrma(selected)
            avatar=str(self.window.assets.avatar)
            self.window.store.set_animation(avatar,slot,str(selected))
            if self.window.store.animations(avatar).get(slot)!=str(selected):
                raise sqlite3.DatabaseError("Die Zuordnung konnte nicht überprüft werden")
        except (InvalidAnimation,OSError):
            self.cancelAnimation(slot)
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"VRMA ist nicht mehr verfügbar. Zuordnung nicht gespeichert."}))
            return
        except sqlite3.Error:
            # Die geprüfte Vorschau bleibt für einen erneuten Speicherversuch erhalten.
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Animationszuordnung konnte nicht in SQLite gespeichert werden."}))
            return
        self.window.assets.staged_animations.pop(slot,None)
        self.pending_names.pop(slot,None)
        self.uiEvent.emit(json.dumps({"type":"animation_changed","slot":slot,
            "filename":"Benutzerdefiniert","availableAlternatives":Bridge._available_alternatives(self),"availablePrimary":Bridge._available_primary(self)}))
    @Slot(str)
    def cancelAnimation(self,slot:str):
        self.window.assets.staged_animations.pop(slot,None)
        self.pending_names.pop(slot,None)
        self.uiEvent.emit(json.dumps({"type":"animation_cancelled","slot":slot}))
    @Slot(str)
    def resetAnimation(self,slot:str):
        if slot not in KNOWN_SLOTS or not self.window.assets.avatar:return
        try:
            self.window.store.reset_animation(str(self.window.assets.avatar),slot)
        except sqlite3.Error:
            self.uiEvent.emit(json.dumps({"type":"error",
                "value":"Standardanimation konnte nicht gespeichert werden."}))
            return
        self.cancelAnimation(slot)
        self.uiEvent.emit(json.dumps({"type":"animation_changed","slot":slot,"filename":None,"availableAlternatives":Bridge._available_alternatives(self),"availablePrimary":Bridge._available_primary(self)}))
    @Slot(str)
    def previewAnimation(self,slot:str):
        if slot in KNOWN_SLOTS:
            self.uiEvent.emit(json.dumps({"type":"preview","slot":slot,
                "staged":slot in self.window.assets.staged_animations}))
    @Slot()
    def prepareAudio(self):
        if self.recording:return
        self.controller.prepare_phrases()
    @Slot()
    def clearChat(self):
        if self.recording:self.close_recording()
        try:self.controller.clear_chat()
        except (OSError,sqlite3.Error):
            self.controller.stop()
            self.uiEvent.emit(json.dumps({"type":"error","value":"Verlauf konnte nicht geloescht werden."}))
    @Slot(bool)
    def setDockOpen(self,opened:bool):
        self.window.set_dock_open(opened)
    @Slot(result="QVariantList")
    def cursorPosition(self):
        """Return only a bounded, view-relative cursor point; never store it."""
        if not self.window.isVisible() or self.window.isMinimized():
            return []
        view=self.window.view
        point=view.mapFromGlobal(QCursor.pos())
        w,h=max(1,view.width()),max(1,view.height())
        return [max(-2*w,min(3*w,point.x())),max(-2*h,min(3*h,point.y()))]

    @Slot()
    def dragWindow(self):
        handle=self.window.windowHandle()
        if handle:handle.startSystemMove()
    @Slot()
    def minimize(self):self.window.showMinimized()
    @Slot()
    def closeApp(self):self.window.close()
    @Slot()
    def toggleAlwaysOnTop(self):
        self.saveSetting("always_on_top",json.dumps(not self.window.store.get_settings()["always_on_top"]))
    @Slot()
    def stopAudio(self):
        if self.recording:self.close_recording()
        self.controller.stop()

def local_page_url(url,port):
    return (url.scheme()=="http" and url.host()=="127.0.0.1"
            and url.port()==port and not url.userInfo())


class MiniPage(QWebEnginePage):
    def __init__(self,port,parent=None):
        super().__init__(parent)
        self.port=port
        # Die aktuelle App nimmt ausschliesslich ueber PortAudio auf.
        # Chromium darf keine Mikrofon-/Kamera-/Bildschirmrechte erhalten.
        self.permissionRequested.connect(self._deny_permission)

    @staticmethod
    def _deny_permission(permission):
        permission.deny()

    def acceptNavigationRequest(self,url,navigation_type,is_main_frame):
        return local_page_url(url,self.port)


class MiniWindow(QMainWindow):
    def __init__(self,dist:Path,store:Store|None=None):
        super().__init__()
        self.store=store or Store()
        self.controller=Controller(self.store,self)
        self.assets=WebAssets(dist,self.store)
        self.server_port=self.assets.start()
        self.player=AudioPlayer(self)
        self.bridge=Bridge(self)
        self._cache_timer=QTimer(self)
        self._cache_timer.setInterval(60000)
        self._cache_timer.timeout.connect(self._maintain_cache)
        self._cache_timer.start()
        QTimer.singleShot(0,self._maintain_cache)
        self.setWindowTitle("OMOI Mini")
        settings=self.store.get_settings()
        self.resize(int(settings.get("width",520)),int(settings.get("height",720)))
        self._dock_open=False
        self._compact_width=self.width()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        flags=Qt.WindowType.FramelessWindowHint|Qt.WindowType.Window
        if settings.get("always_on_top",True):flags|=Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.view=QWebEngineView(self)
        self.view.setPage(MiniPage(self.server_port,self.view))
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.view.setStyleSheet("background: transparent;")
        self.view.page().setBackgroundColor(QColor(0,0,0,0))
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,False)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,False)
        self.channel=QWebChannel(self.view.page())
        self.channel.registerObject("bridge",self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        self.view.setUrl(QUrl(f"http://127.0.0.1:{self.server_port}/"))
        self.setStyleSheet("background:transparent;")
    def _maintain_cache(self):
        if not self.bridge.recording and not self.player.voice_busy():
            self.controller.maintain_audio_cache()

    def set_dock_open(self,opened:bool):
        """Expand beside the avatar, never beyond the current screen."""
        if opened==self._dock_open:
            return
        self._dock_open=opened
        if self.isMaximized() or self.isFullScreen():
            return
        screen=self.screen()
        if screen is None:
            return
        if opened:
            self._compact_width=self.width()
        current=self.geometry()
        bounds=screen.availableGeometry()
        target=dock_geometry(
            Rect(current.x(),current.y(),current.width(),current.height()),
            Rect(bounds.x(),bounds.y(),bounds.width(),bounds.height()),
            opened,self._compact_width)
        self.setGeometry(target.x,target.y,target.width,target.height)

    def set_always_on_top(self,enabled:bool):
        flag=Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlag(flag,enabled)
        self.show()
    def closeEvent(self,event):
        try:
            for release in (self._cache_timer.stop,self.bridge.close_recording,self.controller.close,
                            self.player.stop,self.assets.close):
                try:release()
                except Exception:pass
        finally:super().closeEvent(event)

def start_gui(dist:Path)->int:
    import sys
    app=QApplication(sys.argv)
    app.setApplicationName("OMOI Mini")
    if not (dist/"index.html").is_file():
        print("Web-Oberfläche nicht gebaut. Bitte 'npm run build --prefix web' ausführen.")
        return 2
    try:
        store=Store()
        store.get_settings()
    except (ValueError,OSError,sqlite3.Error):
        QMessageBox.critical(None,"OMOI Mini","Die lokale Datenbank oder Einstellungen sind ungueltig. Bitte Profil pruefen. Es wurde kein Anbieter gestartet.")
        return 2
    window=MiniWindow(dist,store)
    window.show()
    return app.exec()
