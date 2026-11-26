"""Gesprächskontrolle, sichere Werkzeuge, TTS-Cache und asynchrone Anbieter."""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import wave
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from .audio_cache import prune_cache,touch_cached,cache_ready,prepare_cache_target
from .core import Store,classify_preset,deterministic_action,infer_emotion,normalize_stt_text,user_data_dir
from .providers import LocalGemma,ProviderFailure,cloud_answer_stream,transcribe_openai,cartesia_wav
from .local_stt import LocalWhisper
from .local_tts import local_tts_wav,local_tts_pcm_stream,LocalTTSProcess,local_tts_model_id,local_tts_default_speed
from .piper_tts import PiperTTSProcess
from .streaming import SpeechChunker
from .speech_text import speech_text
from .runtime_config import runtime_overrides
from .stt_quality import PartialStabilizer, correct_domain, decoder_options
from .stt_audio_guard import analyze_wav

class Controller(QObject):
    event=Signal(str)
    audio_ready=Signal(str,int)
    audio_enqueue=Signal(str,int)
    timer_audio=Signal(str,int)
    stop_audio=Signal(int)
    audio_stream_start=Signal(str,int,int)
    audio_stream_chunk=Signal(str,bytes)
    audio_stream_finish=Signal(str)
    audio_stream_abort=Signal(str)
    def __init__(self,store:Store,parent=None):
        super().__init__(parent)
        self.store=store
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix="omoi-task")
        self.tts_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="omoi-speech")
        self.stt_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="omoi-stt")
        llm_settings=store.get_settings()
        self.qwen_variant=llm_settings.get("qwen_variant","base")
        self._qwen_options={"variant":self.qwen_variant} if self.qwen_variant!="base" else {}
        self.active_local_profile=llm_settings.get("llm_local_profile","standard")
        gemma12=self.active_local_profile=="gemma12"
        self.gemma=LocalGemma(llm_settings.get("llm_gemma12_file" if gemma12 else "llm_model_file",""),
                              (llm_settings.get("llm_gemma12_server_file") or llm_settings.get("llm_server_file","")) if gemma12 else llm_settings.get("llm_server_file",""),strict_owned=gemma12)
        overrides=runtime_overrides(llm_settings)
        self.whisper=LocalWhisper(runtime_overrides=overrides)
        self.cuda_whisper=LocalWhisper(engine="cuda",runtime_overrides=overrides)
        self.local_voice=LocalTTSProcess(
            reference_path=llm_settings.get("qwen_voice_reference",""),
            runtime_overrides=overrides,variant=self.qwen_variant)
        self.piper_voice=PiperTTSProcess(runtime_overrides=overrides)
        self._pending_review=None
        self._generation=0
        self._voice_jobs=0
        self._pending_voice={}
        self._voice_revision=0
        self._closing=False
        self._stack_warming=False
        self._release_retry=set()
        self._lock=threading.Lock()
        self._timer:threading.Timer|None=None
        self._partial_lock=threading.Lock()
        self._partial_inflight=False
        self._partial_turn=-1
        self._partial_closed_turn=-1
        self._final_audio_turn=-1
        self._partial_last_text=""
        self._partial_last_bytes=0
        self._partial_stabilizer=PartialStabilizer()
        self.gemma.prepare_voice=self._prepare_gpu_voice_for_llm
    def emit(self,kind:str,**data):
        self.event.emit(json.dumps({"type":kind,**data},ensure_ascii=False))
    def current(self,turn:int)->bool:
        with self._lock:return not self._closing and turn==self._generation
    def _voice_snapshot_locked(self):
        self._voice_revision+=1
        return {"revision":self._voice_revision,"busy":self._voice_jobs>0}

    def voice_busy(self)->bool:
        with self._lock:
            return self._voice_jobs>0 or self._pending_review is not None

    def _submit_voice(self,pool,turn,fn,*args,**kwargs):
        ticket=object()
        with self._lock:
            if self._closing or turn!=self._generation:
                return
            self._pending_voice[ticket]=(fn,args,kwargs)
            self._voice_jobs+=1
            snapshot=self._voice_snapshot_locked()
        self.emit("voice_work",**snapshot)

        def run():
            # Nur der Ticket-Schluessel bleibt im Executor. Bei Stop
            # werden wartende Audio-/Textdaten sofort aus dem RAM entfernt.
            with self._lock:
                job=self._pending_voice.pop(ticket,None)
            if job is None:return
            try:
                if self.current(turn):
                    action,parameters,options=job
                    return action(*parameters,**options)
            finally:
                with self._lock:
                    if turn!=self._generation:
                        snapshot=None
                    else:
                        self._voice_jobs-=1
                        snapshot=self._voice_snapshot_locked()
                if snapshot is not None:
                    self.emit("voice_work",**snapshot)
        try:
            return pool.submit(run)
        except Exception:
            with self._lock:
                self._pending_voice.pop(ticket,None)
                if turn==self._generation:
                    self._voice_jobs-=1
                    snapshot=self._voice_snapshot_locked()
                else:
                    snapshot=None
            if snapshot is not None:
                self.emit("voice_work",**snapshot)
            raise

    def _advance_turn_locked(self):
        self._generation+=1
        pending=self._pending_review
        self._pending_review=None
        self._voice_jobs=0
        self._pending_voice.clear()
        # Nur gespeicherte Nachrichten gehoeren in den Verlauf.
        history=None
        if not self._closing:
            try:history=self.store.messages(35)
            except (OSError,sqlite3.Error):pass  # Stop bleibt auch bei DB-Fehlern wirksam.
        return self._generation,pending,self._voice_snapshot_locked(),history

    def _announce_turn(self,turn,pending,snapshot,history):
        self.emit("voice_work",**snapshot)
        if pending:
            self.emit("stt_review_clear",turn=turn)
        self.emit("animation_reset",turn=turn,history=history)
        self.stop_audio.emit(turn)

    def next_turn(self)->int:
        with self._lock:
            turn,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(turn,pending,snapshot,history)
        return turn

    def change_turn_setting(self,key,value):
        """Konfiguration und Abbruch gemeinsam unter demselben Lock."""
        with self._lock:
            if self._closing:raise ValueError("Anwendung wird beendet.")
            before=self.store.get_settings()
            previous=before.get(key)
            self.store.set_setting(key,value)
            if previous==value:return False
            try:after=self.store.get_settings()
            except (ValueError,OSError,sqlite3.Error):after=None
            if after is not None and self._gpu_speech_selection(after)-self._gpu_speech_selection(before):
                self.gemma.request_refit()
            turn,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(turn,pending,snapshot,history)
        self.emit("status",turn=turn,value="Bereit",avatarState="idle")
        if after is None:
            self.emit("error",value="Lokale Laufzeiten konnten nicht geprueft werden.")
        else:self._schedule_unused_models(before,after)
        return True

    @staticmethod
    def _gpu_speech_selection(settings):
        selected=set()
        if settings["stt"]=="cuda":selected.add("cuda")
        if settings["speak"] and settings["tts"]=="local":selected.add("qwen")
        return selected

    def _selected_local_models(self,settings):
        """Nur ausgewaehlte, zur laufenden Konfiguration passende Adapter."""
        selected=set()
        if settings["llm"]=="local" and self._local_llm_matches(settings):
            selected.add("llm")
        for key,provider,engine in (("cuda","cuda",self.cuda_whisper),
                                    ("openvino","local",self.whisper)):
            if settings["stt"]==provider and engine.runtime_overrides==runtime_overrides(settings):
                selected.add(key)
        if settings["speak"]:
            if settings["tts"]=="local" and self._local_tts_matches(settings):
                selected.add("qwen")
            if settings["tts"]=="piper" and self.piper_voice.runtime_overrides==runtime_overrides(settings):
                selected.add("piper")
        return selected

    def _schedule_unused_models(self,before,after):
        try:
            selected=self._selected_local_models(after)
            removed=self._selected_local_models(before)-selected
        except (ValueError,OSError,sqlite3.Error):
            self.emit("error",value="Lokale Laufzeiten konnten nicht geprueft werden.")
            return
        # Ein fehlgeschlagener Stop behaelt sein Prozesshandle. Beim
        # naechsten echten Anbieterwechsel erneut versuchen, nie neu laden.
        with self._lock:
            if self._closing:return
            removed.update(self._release_retry-selected)
            self._release_retry.difference_update(removed)
        adapters=(("llm",self.gemma,self.pool),("cuda",self.cuda_whisper,self.stt_pool),
                  ("openvino",self.whisper,self.stt_pool),("qwen",self.local_voice,self.tts_pool),
                  ("piper",self.piper_voice,self.tts_pool))
        for name,engine,pool in adapters:
            if name not in removed:continue
            try:pool.submit(self._release_unused_model,name,engine)
            except RuntimeError:
                self._release_failed(name)

    def _release_unused_model(self,name,engine):
        def unused():
            with self._lock:
                return not self._closing and name not in self._selected_local_models(self.store.get_settings())
        try:
            # Adapter-Lock schuetzt gegen einen gleichzeitigen Neustart.
            # Bei schneller Rueckwahl wird der alte Auftrag uebersprungen.
            engine.unload_if(unused)
        except Exception:
            self._release_failed(name)

    def _release_failed(self,name):
        with self._lock:
            if self._closing:return
            self._release_retry.add(name)
        self.emit("error",value="Lokale Laufzeit konnte nicht freigegeben werden.")

    def send_text(self,text:str,source:str="keyboard"):
        if self.stack_warming():return
        text=text.strip()
        if not text or len(text)>4000:
            self.emit("error",value="Bitte eine Nachricht mit höchstens 4000 Zeichen eingeben.")
            return
        turn=self.next_turn()
        self._dispatch_text(turn,text,source)

    def _dispatch_text(self,turn,text,source):
        text=text.strip()
        if not 0<len(text)<=4000:
            if self.current(turn):
                self.emit("error",turn=turn,value="Transkript zu lang oder leer.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
            return
        try:
            with self._lock:
                if self._closing or turn!=self._generation:return
                self.store.add_message("user",text,source)
        except (OSError,sqlite3.Error):
            if self.current(turn):
                self.emit("error",turn=turn,value="Nachricht konnte nicht gespeichert werden.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
            return
        if not self.current(turn):return
        self.emit("message",turn=turn,role="user",content=text)
        self.emit("status",turn=turn,value="Denkt…",avatarState="thinking")
        try:self._submit_voice(self.pool,turn,self._process,turn,text)
        except RuntimeError:
            if self.current(turn):
                self.emit("error",turn=turn,value="Anfrage konnte nicht gestartet werden.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
    def _show_stt_review(self,turn,engine,language,text):
        text=text.strip()
        if not 0<len(text)<=4000:
            raise ProviderFailure("Transkript zu lang oder leer.")
        suggestion=self.store.stt_suggestion(engine,language,text)
        with self._lock:
            if turn!=self._generation:
                return
            self._pending_review=(turn,engine,language,text)
        if self.current(turn):
            self.emit("stt_review",turn=turn,text=text,suggestion=suggestion or text)
            self.emit("status",turn=turn,value="Transkript pr\u00fcfen",avatarState="idle")

    def submit_stt_review(self,turn:int,text:str,remember:bool)->bool:
        text=text.strip()
        if not 0<len(text)<=4000 or type(remember) is not bool:
            return False
        with self._lock:
            pending=self._pending_review
            if not pending or pending[0]!=turn or turn!=self._generation:
                return False
            _,engine,language,original=pending
            if remember and text!=original:
                try:
                    self.store.remember_stt_correction(engine,language,original,text)
                except (ValueError,OSError,sqlite3.Error):
                    return False
            new_turn,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(new_turn,pending,snapshot,history)
        self._dispatch_text(new_turn,text,"microphone")
        return True

    def cancel_stt_review(self,turn:int)->bool:
        with self._lock:
            if not self._pending_review or self._pending_review[0]!=turn or turn!=self._generation:
                return False
            new_turn,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(new_turn,pending,snapshot,history)
        self.emit("status",turn=new_turn,value="Bereit",avatarState="idle")
        return True

    def _timer_action(self,text:str,lang:str,turn:int|None=None)->tuple[str,str]|None:
        low=text.strip().lower()
        if re.search(r"(?:stoppe|lösche|abbrechen|останови|отмени).*(?:timer|таймер)",low):
            with self._lock:
                if self._closing or (turn is not None and turn!=self._generation):return None
                if self._timer:self._timer.cancel();self._timer=None
            return ("timer","Timer wurde abgebrochen." if lang=="de" else "Таймер отменён.")
        m=re.search(r"(?:timer (?:auf|für)|таймер на)\s+(\d+|zehn|fünf|drei|eine[nr]?|zwei)\s*(sekunden?|minuten?|stunden?|секунд[ыу]?|минут[уы]?)",low)
        if not m:return None
        value=m.group(1)
        number={"zehn":10,"fünf":5,"drei":3,"eine":1,"einen":1,"einer":1,"zwei":2}.get(value)
        number=number if number is not None else int(value)
        unit=m.group(2)
        duration=number*(3600 if unit.startswith("stund") else 60 if unit.startswith(("minut","минут")) else 1)
        if not 0<duration<=3600*12:
            return ("timer","Bitte einen Timer von maximal zwölf Stunden wählen.")
        def alarm():
            # Ein ersetzter oder beendeter Timer darf nichts mehr ausgeben.
            with self._lock:
                if self._closing or self._timer is not timer:return
                self._timer=None
                alarm_turn=self._generation
            try:
                text=self.store.phrase("timer_end",lang)
                self.emit("notice",value=text)
                if self.voice_busy() or self.stack_warming():return
                settings=self.store.get_settings()
                chosen=settings["tts"]
                # Nur vorhandenen Cache nutzen, niemals eine Synthese starten.
                if settings["speak"] and chosen in ("cartesia","local","piper") and speech_text(text):
                    cached=self._cache_path(text,lang,chosen,"neutral")
                    if cache_ready(cached) and self.current(alarm_turn):
                        touch_cached(cached)
                        self.timer_audio.emit(str(cached),alarm_turn)
            except Exception:
                if self.current(alarm_turn):self.emit("error",value="Timer-Ausgabe fehlgeschlagen.")
        with self._lock:
            if self._closing or (turn is not None and turn!=self._generation):return None
            if self._timer:self._timer.cancel()
            timer=threading.Timer(duration,alarm)
            timer.daemon=True
            self._timer=timer
            timer.start()
        return ("timer",f"Timer auf {number} {unit} gestellt." if lang=="de" else f"Таймер на {number} {unit} установлен.")
    def _local_llm_matches(self, options):
        profile=options["llm_local_profile"]
        model_key="llm_gemma12_file" if profile=="gemma12" else "llm_model_file"
        server=(options["llm_gemma12_server_file"] or options["llm_server_file"]) if profile=="gemma12" else options["llm_server_file"]
        return (profile==self.active_local_profile and
                self.gemma.model_file==options[model_key] and self.gemma.server_file==server)

    def stack_warming(self):
        with self._lock:return self._stack_warming

    def prewarm_stack(self):
        """Ein manueller Auftrag, ausgewaehlte lokale Komponenten nacheinander."""
        try:
            with self._lock:
                if self._closing or self._stack_warming or self._voice_jobs or self._pending_review:
                    return
                # Die Auswahl beim Klick festhalten, nicht erst im Worker.
                settings=self.store.get_settings()
                self._stack_warming=True
                turn=self._generation
        except (ValueError,OSError,sqlite3.Error):
            self.emit("stack_warmup",state="error",component="",errors=[])
            return
        self.emit("stack_warmup",state="loading",component="")
        try:self.pool.submit(lambda:self._prewarm_stack_worker(settings,turn))
        except RuntimeError:
            with self._lock:self._stack_warming=False
            self.emit("stack_warmup",state="error",component="",errors=[])

    def cancel_stack_warmup(self):
        if self.stack_warming():self.stop()

    def _prewarm_stack_worker(self,settings=None,turn=None):
        if turn is None:
            with self._lock:turn=self._generation
        active=lambda:self.current(turn)
        errors=[]
        completed=[]
        tasks=[]
        try:
            if settings is None:settings=self.store.get_settings()
            tasks=self._stack_tasks(settings,active)
            for component,task in tasks:
                if not active():raise ProviderFailure("Lokaler Start abgebrochen.")
                if self._stack_signature(self.store.get_settings())!=self._stack_signature(settings):
                    raise ProviderFailure("Modellauswahl geändert. Bitte erneut Start drücken.")
                self.emit("stack_warmup",state="loading",component=component,completed=completed[:])
                try:
                    task()
                    if not active():raise ProviderFailure("Lokaler Start abgebrochen.")
                    completed.append(component)
                except ProviderFailure as exc:
                    errors.append({"component":component,"message":str(exc)})
                except Exception:
                    errors.append({"component":component,"message":"Lokaler Start fehlgeschlagen."})
            if not active():raise ProviderFailure("Lokaler Start abgebrochen.")
            if self._stack_signature(self.store.get_settings())!=self._stack_signature(settings):
                raise ProviderFailure("Modellauswahl geändert. Bitte erneut Start drücken.")
        except ProviderFailure as exc:
            errors.append({"component":"Start","message":str(exc)})
        except Exception:
            errors.append({"component":"Start","message":"Lokaler Start fehlgeschlagen."})
        finally:
            with self._lock:
                self._stack_warming=False
                closing=self._closing
            if not closing:
                self.emit("stack_warmup",state="error" if errors else "ready" if tasks else "cloud",
                          component="",completed=completed,errors=errors)

    @staticmethod
    def _stack_signature(settings):
        keys=("stt","tts","speak","llm_mode","llm_local_profile","llm_model_file","llm_server_file",
              "llm_gemma12_file","llm_gemma12_server_file","qwen_variant","qwen_voice_reference",
              "language","piper_voice_de","piper_voice_ru")
        return tuple(settings[k] for k in keys),runtime_overrides(settings)

    def _local_tts_matches(self,settings):
        return (self.qwen_variant==settings["qwen_variant"] and
                self.local_voice.reference_path==settings["qwen_voice_reference"] and
                self.local_voice.runtime_overrides==runtime_overrides(settings))

    def _stack_tasks(self,settings,is_active=lambda:True,*,refit=True):
        tasks=[]
        if settings["stt"] in ("cuda","local"):
            recognizer=self.cuda_whisper if settings["stt"]=="cuda" else self.whisper
            def stt():
                if recognizer.runtime_overrides!=runtime_overrides(settings):
                    raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
                if refit and settings["stt"]=="cuda":self.gemma.release_for_refit(is_active)
                with recognizer._request_lock(is_active):recognizer.ensure(is_active=is_active)
            tasks.append(("STT",stt))
        if settings["speak"] and settings["tts"]=="local":
            def tts():
                if not self._local_tts_matches(settings):
                    raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
                if refit:self.gemma.release_for_refit(is_active)
                self.local_voice.prewarm(is_active)
            tasks.append(("TTS",tts))
        elif settings["speak"] and settings["tts"]=="piper":
            def piper():
                if self.piper_voice.runtime_overrides!=runtime_overrides(settings):
                    raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
                lang=settings["language"]
                voice=settings["piper_voice_ru" if lang=="ru" else "piper_voice_de"]
                self.piper_voice.synthesize("Hallo!" if lang=="de" else "\u041f\u0440\u0438\u0432\u0435\u0442!",lang,voice,is_active=is_active)
            tasks.append(("TTS",piper))
        if settings["llm"]=="local":
            def llm():
                if not self._local_llm_matches(settings):
                    raise ProviderFailure("Lokales Modell geändert. Bitte OMOI Mini neu starten.")
                self.gemma.ensure(is_active)
            tasks.append(("LLM",llm))
        return tasks

    def _prepare_gpu_voice_for_llm(self,is_active):
        """First local answer: allocate selected GPU speech models before Auto-fit."""
        with self._lock:
            turn=self._generation
        settings=self.store.get_settings()
        for component,task in self._stack_tasks(settings,is_active,refit=False):
            gpu_stt=(component=="STT" and settings["stt"]=="cuda"
                     and not self.cuda_whisper.is_ready())
            gpu_tts=(component=="TTS" and settings["speak"] and settings["tts"]=="local")
            if not (gpu_stt or gpu_tts):continue
            if not is_active():raise ProviderFailure("Lokaler Start abgebrochen.")
            try:
                task()
            except Exception as exc:
                if not is_active():raise ProviderFailure("Lokaler Start abgebrochen.") from None
                # Speech can be unavailable while local text remains usable.
                message=str(exc) if isinstance(exc,ProviderFailure) else "Lokaler Start fehlgeschlagen."
                if self.current(turn):self.emit("error",turn=turn,value=message)
        if not is_active():raise ProviderFailure("Lokaler Start abgebrochen.")

    def _stream_llm(self,turn:int,context:list[dict[str,str]],lang:str,choice:str)->str|None:
        """Text-Deltas sofort anzeigen; Satz-Audio parallel in TTS-Warteschlange senden."""
        chunks=[]
        chunker=SpeechChunker()
        started=time.monotonic()
        first_token=False
        first_sentence=False
        options=self.store.get_settings()
        if not self.current(turn):return None
        answer_length=0
        speaking=bool(options["speak"] and options["tts"] in ("local","cartesia","piper"))
        if choice=="local" and not self._local_llm_matches(options):
            raise ProviderFailure("Lokales Modell geändert. Bitte OMOI Mini neu starten.")
        self.emit("stream_start",turn=turn)
        active=lambda:self.current(turn)
        stream=(self.gemma.stream_answer(context,lang,active)
                if choice=="local" else cloud_answer_stream(context,lang,active))
        try:
            for piece in stream:
                if not self.current(turn):
                    return None
                # Verlauf, UI und Sprachqueue verwenden dieselbe Obergrenze.
                if not isinstance(piece,str):
                    raise ProviderFailure("Ungueltiger LLM-Text")
                remaining=10000-answer_length
                piece=piece[:remaining]
                answer_length+=len(piece)
                chunks.append(piece)
                if not first_token and piece:
                    first_token=True
                    self.emit("latency",turn=turn,stage="llm_first_token",
                              elapsedMs=round((time.monotonic()-started)*1000))
                self.emit("stream_delta",turn=turn,text=piece)
                if speaking:
                    for sentence in chunker.feed(piece):
                        sentence=speech_text(sentence)
                        if not sentence:continue
                        stream_pcm=not first_sentence
                        if stream_pcm:
                            first_sentence=True
                            self.emit("latency",turn=turn,stage="llm_first_speech_segment",
                                      elapsedMs=round((time.monotonic()-started)*1000))
                        self._submit_voice(self.tts_pool,turn,self._speak,sentence,lang,turn,
                                           enqueue=True,stream_pcm=stream_pcm)
                if answer_length>=10000:break
        finally:
            close=getattr(stream,"close",None)
            if close is not None:close()
        if not self.current(turn):return None
        for sentence in chunker.finish() if speaking else []:
            sentence=speech_text(sentence)
            if not sentence:continue
            self._submit_voice(self.tts_pool,turn,self._speak,sentence,lang,turn,
                               enqueue=True,stream_pcm=not first_sentence)
            first_sentence=True
        answer="".join(chunks).strip()
        if not answer:raise ProviderFailure("Das Sprachmodell hat keinen Antworttext geliefert")
        self.emit("latency",turn=turn,stage="llm_complete",
                  elapsedMs=round((time.monotonic()-started)*1000))
        return answer

    def _process(self,turn:int,text:str):
        try:
            lang=self.store.get_settings()["language"]
            preset=classify_preset(text)
            if preset:
                answer=self.store.phrase(preset,lang)
                provider="preset"
                slot="explaining" if preset=="introduce" else "welcome"
            else:
                if not self.current(turn):return
                tool=self._timer_action(text,lang,turn)
                if not self.current(turn):return
                with self._lock:
                    if self._closing or turn!=self._generation:return
                    tool=tool or deterministic_action(text,self.store,lang)
                if tool:
                    provider="python/"+tool[0]
                    answer=tool[1]
                    slot="nod"
                else:
                    choice=self.store.get_settings()["llm"]
                    context=self.store.messages(10)
                    if not self.current(turn):return
                    answer=self._stream_llm(turn,context,lang,choice)
                    if answer is None:return
                    provider="gemma-local" if choice=="local" else "openai-luna"
                    slot="talking"
            # Prüfung und Speicherung gehören zusammen: Stop darf keinen
            # veralteten Assistententext mehr in den Verlauf schreiben.
            with self._lock:
                if self._closing or turn!=self._generation:return
                self.store.add_message("assistant",answer,provider)
            if not self.current(turn):return
            if provider not in ("gemma-local","openai-luna"):
                self.emit("message",turn=turn,role="assistant",content=answer,provider=provider)
            else:
                self.emit("stream_end",turn=turn,text=answer)
            self.emit("response",turn=turn,text=answer,animation=slot,emotion=infer_emotion(answer))
            speech_settings=self.store.get_settings()
            if not self.current(turn):return
            if speech_text(answer) and speech_settings["speak"] and speech_settings["tts"] in ("cartesia","local","piper"):
                if provider not in ("gemma-local","openai-luna"):
                    self._submit_voice(self.tts_pool,turn,self._speak,answer,lang,turn,emotion=infer_emotion(answer))
            elif self.current(turn):
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
        except ProviderFailure as exc:
            self._fail_response(turn,str(exc))
        except Exception:
            self._fail_response(turn,"Die Anfrage konnte nicht verarbeitet werden.")

    def _fail_response(self,turn,message):
        # Bei einem LLM-/DB-Fehler auch bereits wartende Satz-Audios
        # abbrechen. Ein Fehler eines alten Turns darf keinen neuen stoppen.
        with self._lock:
            if self._closing or turn!=self._generation:return
            fresh,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(fresh,pending,snapshot,history)
        self.emit("stream_abort",turn=fresh)
        self.emit("error",turn=fresh,value=message)
        self.emit("status",turn=fresh,value="Fehler",avatarState="idle")

    def _cartesia_options(self,settings=None):
        if settings is None:settings=self.store.get_settings()
        voice_id=settings.get("cartesia_voice_id","") or os.environ.get("CARTESIA_VOICE_ID","").strip()
        return {"voice_id":voice_id} if voice_id else {}

    def _cache_path(self,text:str,lang:str,tts:str,emotion:str="neutral",*,cartesia_options=None,settings=None)->Path:
        """Herkunft, Sprachprofil und Stimmung beeinflussen den Cache-Schlüssel."""
        text=speech_text(text)
        if settings is None:settings=self.store.get_settings()
        if tts=="cartesia":
            # Rückwärtskompatibel mit bereits vorbereiteten Nika-Phrasen.
            options=self._cartesia_options(settings) if cartesia_options is None else cartesia_options
            voice=options.get("voice_id")
            identity="cartesia:"+voice if voice else "Nika"
            signature=identity+"|sonic-3.6|"+lang+"|"+text
        elif tts=="local":
            reference=settings.get("qwen_voice_reference","")
            signature=(local_tts_model_id(self.qwen_variant)+"|reference:"+reference+"|speed-"+
                       str(local_tts_default_speed(lang,self.qwen_variant))+"|"+emotion+"|"+lang+"|"+text)
        elif tts=="piper":
            voice=settings["piper_voice_ru" if lang=="ru" else "piper_voice_de"]
            signature="piper|"+voice+"|"+lang+"|"+text
        else:
            raise ProviderFailure("Keine Sprachausgabe gewählt")
        digest=hashlib.sha256(signature.encode("utf-8")).hexdigest()
        return user_data_dir()/"audio-cache"/(digest+".wav")
    @staticmethod
    def _write_cache(path,data):
        """Auch bei einem Schreibfehler keine halbe WAV-Datei hinterlassen."""
        import tempfile
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,suffix=".tmp",delete=False) as handle:
                temporary=Path(handle.name)
                handle.write(data)
            temporary.replace(path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)

    def _speak(self,text:str,lang:str,turn:int|None,emotion:str="neutral",enqueue:bool=False,
               stream_pcm:bool=True):
        if turn is None:
            with self._lock:turn=self._generation
        if not self.current(turn):return
        try:
            settings=self.store.get_settings()
            if not self.current(turn):return
            selected=settings["tts"]
            if selected=="none" or not settings["speak"]:return
            text=speech_text(text)
            if not text:return
            cartesia_options=self._cartesia_options(settings) if selected=="cartesia" else {}
            if selected=="local" and not self._local_tts_matches(settings):
                raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
            if selected=="piper" and self.piper_voice.runtime_overrides!=runtime_overrides(settings):
                raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
            path=self._cache_path(text,lang,selected,emotion,cartesia_options=cartesia_options,settings=settings)
            prepare_cache_target(path)
            if not self.current(turn):return
            if not cache_ready(path):
                if selected=="local":
                    self.gemma.release_for_refit(lambda:self.current(turn))
                    self.local_voice.ensure(lambda:self.current(turn))
                    if not self.current(turn):return
                    if enqueue and local_tts_default_speed(lang,self.qwen_variant)==1.0:
                        token=f"{turn}:{time.monotonic_ns()}"
                        started=time.monotonic()
                        first=[False]
                        def push_pcm(data:bytes):
                            if not stream_pcm or not self.current(turn):
                                return
                            if not first[0]:
                                self.audio_stream_start.emit(token,24000,turn)
                                first[0]=True
                                self.emit("latency",turn=turn,stage="tts_first_pcm",
                                          elapsedMs=round((time.monotonic()-started)*1000))
                            self.audio_stream_chunk.emit(token,data)
                        try:
                            pcm=local_tts_pcm_stream(
                                text,lang,emotion,push_pcm,
                                is_active=lambda:self.current(turn), **self._qwen_options,
                            )
                            if pcm is None or not self.current(turn):
                                if first[0]:self.audio_stream_abort.emit(token)
                                return
                            if first[0]:self.audio_stream_finish.emit(token)
                        except Exception:
                            if first[0]:self.audio_stream_abort.emit(token)
                            raise
                        data=io.BytesIO()
                        with wave.open(data,"wb") as wav:
                            wav.setnchannels(1)
                            wav.setsampwidth(2)
                            wav.setframerate(24000)
                            wav.writeframes(pcm)
                        if self.current(turn):
                            self._write_cache(path,data.getvalue())
                            # Nur der erste Satz gelangt live in Qt. Folgesaetze
                            # bleiben als fertige WAV-Dateien in der FIFO statt
                            # als zahlreiche grosse PCM-Puffer im GUI-RAM.
                            if not first[0] and self.current(turn):
                                touch_cached(path)
                                self.audio_enqueue.emit(str(path),turn)
                        return
                    binary=local_tts_wav(text,lang,emotion,is_active=lambda:self.current(turn),**self._qwen_options)
                elif selected=="piper":
                    voice=settings["piper_voice_ru" if lang=="ru" else "piper_voice_de"]
                    binary=self.piper_voice.synthesize(text,lang,voice,is_active=lambda:self.current(turn))
                else:
                    binary=cartesia_wav(text,lang,is_active=lambda:self.current(turn),**cartesia_options)
                if binary is None or not self.current(turn):return
                self._write_cache(path,binary)
            if self.current(turn):
                touch_cached(path)
                if enqueue:self.audio_enqueue.emit(str(path),turn)
                else:self.audio_ready.emit(str(path),turn)
        except ProviderFailure as exc:
            if self.current(turn):
                self.emit("error",turn=turn,value=str(exc))
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
        except OSError:
            if self.current(turn):
                self.emit("error",turn=turn,value="Sprachcache konnte nicht geschrieben werden.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
        except Exception:
            if self.current(turn):
                self.emit("error",turn=turn,value="Sprachausgabe fehlgeschlagen.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
    def maintain_audio_cache(self):
        """Nur im Leerlauf aufrufen; Standardphrasen der aktuellen Stimme erhalten."""
        if self.voice_busy() or self.stack_warming():return
        try:
            settings=self.store.get_settings()
            provider=settings["tts"]
            protected=set()
            if provider in ("cartesia","local","piper"):
                for lang in ("de","ru"):
                    for name in ("hello","introduce","timer_end"):
                        text=self.store.phrase(name,lang)
                        if speech_text(text):
                            protected.add(self._cache_path(text,lang,provider,"neutral",settings=settings))
            prune_cache(user_data_dir()/"audio-cache",protected)
        except (OSError,ValueError,sqlite3.Error,ProviderFailure):pass

    def startup_greeting(self):
        try:
            settings=self.store.get_settings()
            if not settings["greeting"] or not self.current(0):return
            lang=settings["language"]
            phrase=self.store.phrase("hello",lang)
            self.emit("response",turn=0,text=phrase,animation="welcome",emotion="happy")
            if settings["tts"] in ("cartesia","local","piper") and settings["speak"]:
                if not speech_text(phrase):
                    self.emit("status",turn=0,value="Bereit",avatarState="idle")
                    return
                cached=self._cache_path(phrase,lang,settings["tts"],"neutral")
                if cache_ready(cached):
                    touch_cached(cached)
                    self.audio_ready.emit(str(cached),0)
                else:
                    self.emit("status",turn=0,value="Begrüßungstext (Audio noch nicht vorbereitet)",avatarState="idle")
        except (OSError,ValueError,sqlite3.Error,ProviderFailure):
            if self.current(0):
                self.emit("error",turn=0,value="Sprachausgabe fehlgeschlagen.")
                self.emit("status",turn=0,value="Bereit",avatarState="idle")

    def prepare_phrases(self):
        """Erzeugt eine kleine lokale Audiobibliothek nur nach ausdrücklichem Klick."""
        if self.stack_warming() or self.voice_busy():return
        turn=self.next_turn()
        self.emit("status",turn=turn,value="Bereitet Standardphrasen vor…",avatarState="thinking")
        try:self._submit_voice(self.tts_pool,turn,self._prepare_phrases_worker,turn)
        except RuntimeError:
            if self.current(turn):
                self.emit("error",turn=turn,value="Sprachausgabe fehlgeschlagen.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")

    def _prepare_phrases_worker(self,turn=None):
        if turn is None:
            with self._lock:turn=self._generation
        if not self.current(turn):return
        try:
            settings=self.store.get_settings()
            lang=settings["language"]
            selected=settings["tts"]
            if selected=="local" and not self._local_tts_matches(settings):
                raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
            if selected=="piper" and self.piper_voice.runtime_overrides!=runtime_overrides(settings):
                raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
            cartesia_options=self._cartesia_options(settings) if selected=="cartesia" else {}
            if selected=="none" or not settings["speak"]:
                raise ProviderFailure("Bitte zuerst eine Sprachausgabe auswählen")
            for phrase_name in ("hello","introduce","timer_end"):
                if not self.current(turn):return
                if self._stack_signature(self.store.get_settings())!=self._stack_signature(settings):
                    raise ProviderFailure("Modellauswahl geändert. Bitte erneut Start drücken.")
                text=speech_text(self.store.phrase(phrase_name,lang))
                if not text:continue
                path=self._cache_path(text,lang,selected,"neutral",cartesia_options=cartesia_options,settings=settings)
                prepare_cache_target(path)
                if not self.current(turn):return
                if not cache_ready(path):
                    if selected=="local":
                        self.gemma.release_for_refit(lambda:self.current(turn))
                        self.local_voice.ensure(lambda:self.current(turn))
                        if not self.current(turn):return
                    if selected=="local":
                        audio=local_tts_wav(text,lang,"neutral",is_active=lambda:self.current(turn),**self._qwen_options)
                    elif selected=="piper":
                        voice=settings["piper_voice_ru" if lang=="ru" else "piper_voice_de"]
                        audio=self.piper_voice.synthesize(text,lang,voice,is_active=lambda:self.current(turn))
                    else:
                        audio=cartesia_wav(text,lang,is_active=lambda:self.current(turn),**cartesia_options)
                    if audio is None or not self.current(turn):return
                    self._write_cache(path,audio)
            self.emit("notice",turn=turn,value="Die Standardphrasen wurden gespeichert.")
        except ProviderFailure as exc:
            if self.current(turn):self.emit("error",turn=turn,value=str(exc))
        except OSError:
            if self.current(turn):self.emit("error",turn=turn,value="Audio-Cache konnte nicht gespeichert werden.")
        except Exception:
            if self.current(turn):self.emit("error",turn=turn,value="Sprachausgabe fehlgeschlagen.")
        finally:
            if self.current(turn):self.emit("status",turn=turn,value="Bereit",avatarState="idle")
    def partial_available(self,turn:int)->bool:
        if not self.current(turn):return False
        with self._partial_lock:
            return (not self._partial_inflight and self._partial_closed_turn!=turn
                    and self.cuda_whisper.is_ready())

    def partial_audio(self, wav_bytes:bytes, turn:int) -> bool:
        """CUDA-Whisper waehrend der Aufnahme auswerten, ohne die LLM zu starten.

        Whisper ist ein Batch-Modell: Momentaufnahmen werden im Hintergrund
        ueberschrieben; die letzte, komplette Aufnahme wird final dekodiert.
        Cloud/OpenVINO bleiben unveraendert, keine privaten Partials in SQLite.
        """
        if (not wav_bytes or len(wav_bytes)>16*1024*1024
                or self.store.get_settings()["stt"]!="cuda"
                or not self.current(turn)):
            return False
        with self._partial_lock:
            if self._partial_inflight or self._partial_closed_turn==turn or not self.cuda_whisper.is_ready():
                return False
            if self._partial_turn!=turn:
                self._partial_turn=turn
                self._partial_last_text=""
                self._partial_last_bytes=0
                self._partial_stabilizer.reset()
            # At least two seconds of additional 16k mono PCM per preview.
            if self._partial_last_bytes and len(wav_bytes)-self._partial_last_bytes<64000:
                return False
            self._partial_last_bytes=len(wav_bytes)
            self._partial_inflight=True
        try:
            language=self.store.get_settings()["language"]
            self.stt_pool.submit(self._transcribe_partial, turn, wav_bytes, language)
            return True
        except Exception:
            with self._partial_lock:
                self._partial_inflight=False
            return False

    def _transcribe_partial(self,turn:int,wav_bytes:bytes,language:str):
        try:
            if not self.current(turn):return
            with self._partial_lock:
                if self._partial_closed_turn==turn:return
            settings=self.store.get_settings()
            if not self.current(turn):return
            if settings["stt"]!="cuda" or self.cuda_whisper.runtime_overrides!=runtime_overrides(settings):return
            profile=settings.get("stt_profile","fast")
            if profile=="fast":
                result=self.cuda_whisper.transcribe(wav_bytes,language,partial=True,is_active=lambda:self.current(turn))
            else:
                lang="auto" if settings.get("stt_auto_language") else language
                options=decoder_options(profile,partial=True,custom=settings.get("stt_hotwords",""))
                if lang=="auto":options["preferred_language"]=language
                result=self.cuda_whisper.transcribe(wav_bytes,lang,options=options,partial=True,is_active=lambda:self.current(turn))
            text=normalize_stt_text(result)
            if profile!="fast":
                # Sprachregeln nur fuer die wirklich erkannte Sprache anwenden.
                correction_lang=(self.cuda_whisper.last_language or language) if lang=="auto" else lang
                text,_=correct_domain(text,correction_lang)
            if not text or not self.current(turn):return
            with self._partial_lock:
                if self._partial_closed_turn==turn:return
                if profile=="fast" and text==self._partial_last_text:
                    return
                self._partial_last_text=text
                stable,tail=self._partial_stabilizer.update(text) if profile!="fast" else ("",text)
            if self.current(turn):
                self.emit("stt_partial",turn=turn,text=(stable+tail)[:600],final=False,
                          stable=stable[:600],tail=tail[:600])
        except (ProviderFailure,ValueError,OSError,sqlite3.Error):
            # Eine einzelne unvollstaendige Hypothese darf die Aufnahme nicht
            # abbrechen. Der finale STT-Aufruf meldet einen echten Fehler.
            pass
        finally:
            with self._partial_lock:
                self._partial_inflight=False

    def listen_audio(self,wav_bytes:bytes,recording_turn=None):
        if not wav_bytes:
            self.emit("error",value="Keine Sprache aufgenommen.")
            self.emit("status",value="Bereit",avatarState="idle")
            return
        # Dieselbe Aufnahme bleibt ein Turn. Ein laufender Partial
        # darf fertig werden, ohne den warmen Worker zu beenden.
        if recording_turn is None:
            turn=self.next_turn()
        else:
            turn=recording_turn
            if not self.current(turn):return
            with self._partial_lock:self._partial_closed_turn=turn
        # Doppelte Finish-Signale derselben Aufnahme duerfen nicht zwei
        # STT-/LLM-Auftraege und doppelte Cloud-Kosten erzeugen.
        with self._lock:
            if self._closing or turn!=self._generation or self._final_audio_turn==turn:return
            self._final_audio_turn=turn
        self.emit("status",turn=turn,value="Erkennt Sprache…",avatarState="thinking")
        try:self._submit_voice(self.stt_pool,turn,self._transcribe,turn,wav_bytes)
        except RuntimeError:
            if self.current(turn):
                self.emit("error",turn=turn,value="Spracherkennung konnte nicht gestartet werden.")
                self.emit("status",turn=turn,value="Bereit",avatarState="idle")
    def _transcribe(self,turn:int,data:bytes):
        began=time.monotonic()
        selected="unknown"
        try:
            if not self.current(turn):return
            settings=self.store.get_settings()
            if not self.current(turn):return
            selected=settings["stt"]
            self.emit("stt_stage",turn=turn,stage="start",engine=selected)
            if selected in ("cuda","local"):
                recognizer=self.cuda_whisper if selected=="cuda" else self.whisper
                if recognizer.runtime_overrides!=runtime_overrides(settings):
                    raise ProviderFailure("Laufzeit geändert. Bitte OMOI Mini neu starten.")
            profile=settings.get("stt_profile","fast")
            lang=settings["language"]
            if selected in ("cuda","local") and profile!="fast" and settings.get("stt_speech_guard"):
                diagnostic=analyze_wav(data)
                self.emit("stt_speech_guard",turn=turn,speechLike=diagnostic.speech_like,
                          activeMs=diagnostic.active_ms,voicedMs=diagnostic.voiced_ms)
                if not diagnostic.speech_like:
                    raise ProviderFailure("Keine ausreichend klare Sprache erkannt. Mikrofon oder optionalen Sprachfilter pruefen.")
            if not self.current(turn):return
            if selected=="cuda":
                self.gemma.release_for_refit(lambda:self.current(turn))
                if profile=="fast":
                    result=self.cuda_whisper.transcribe(data,lang,is_active=lambda:self.current(turn))
                else:
                    if settings.get("stt_auto_language"):
                        lang="auto"
                    options=decoder_options(profile,partial=False,custom=settings.get("stt_hotwords",""))
                    if lang=="auto":options["preferred_language"]=settings["language"]
                    result=self.cuda_whisper.transcribe(data,lang,options=options,is_active=lambda:self.current(turn))
            elif selected=="local":
                result=self.whisper.transcribe(data,settings["language"],is_active=lambda:self.current(turn))
            else:
                result=transcribe_openai(data,settings["language"],is_active=lambda:self.current(turn))
            if self.current(turn):
                elapsed=round((time.monotonic()-began)*1000)
                self.emit("latency",turn=turn,stage="stt_complete",elapsedMs=elapsed)
                timing={}
                if selected in ("cuda","local"):
                    timing={"decodeMs":recognizer.last_decode_ms,"loadMs":recognizer.last_load_ms}
                self.emit("stt_stage",turn=turn,stage="complete",engine=selected,elapsedMs=elapsed,**timing)
                text=normalize_stt_text(result)
                if selected in ("cuda","local") and profile!="fast":
                    correction_lang=(self.cuda_whisper.last_language or settings["language"]) if lang=="auto" else lang
                    text,_=correct_domain(text,correction_lang)
                uncertain=selected=="cuda" and self.cuda_whisper.last_uncertain
                if selected in ("cuda","local") and (settings.get("stt_review") or uncertain):
                    # Auto ist eine Anforderungsart, keine Sprache. Korrekturen
                    # nur der tatsaechlich erkannten Sprache zuordnen.
                    review_lang=(recognizer.last_language or settings["language"]) if lang=="auto" else lang
                    self._show_stt_review(turn,selected,review_lang,text)
                else:
                    self._dispatch_text(turn,text,"microphone")
        except ProviderFailure as exc:
            if self.current(turn):
                elapsed=round((time.monotonic()-began)*1000)
                self.emit("stt_stage",turn=turn,stage="error",engine=selected,
                          elapsedMs=elapsed)
                self.emit("error",turn=turn,value=str(exc))
                self.emit("status",turn=turn,value="Fehler",avatarState="idle")
        except Exception:
            # Keine Privatdaten ins Log schreiben. Selbst ein unerwarteter
            # STT-/Windows-Fehler darf nicht endlos als "Erkennt Sprache"
            # in einem unbemerkten Thread stehen bleiben.
            if self.current(turn):
                elapsed=round((time.monotonic()-began)*1000)
                self.emit("stt_stage",turn=turn,stage="error",engine=selected,
                          elapsedMs=elapsed)
                self.emit("error",turn=turn,value="Lokale Spracherkennung fehlgeschlagen. STT-Anbieter und Mikrofon prüfen.")
                self.emit("status",turn=turn,value="Fehler",avatarState="idle")
    def clear_chat(self):
        # Verlauf und Generation atomar wechseln; alte Worker koennen
        # nach dem Loeschen keine Antwort mehr in die Datenbank schreiben.
        with self._lock:
            self.store.clear_messages()
            turn,pending,snapshot,history=self._advance_turn_locked()
        self._announce_turn(turn,pending,snapshot,history)
        self.emit("clear_chat",turn=turn)
        self.emit("status",turn=turn,value="Bereit",avatarState="idle")

    def stop(self)->int:
        turn=self.next_turn()
        self.emit("status",turn=turn,value="Bereit",avatarState="idle")
        return turn
    def close(self):
        with self._lock:
            if self._closing:return
            self._closing=True
            if self._timer:self._timer.cancel();self._timer=None
        self.stop()
        # Ein defekter Adapter darf die restlichen Ressourcen nicht blockieren.
        for release in (self.gemma.close,self.whisper.shutdown,self.cuda_whisper.shutdown,
                        self.local_voice.close,self.piper_voice.shutdown):
            try:release()
            except Exception:
                self.emit("error",value="Eine lokale Laufzeit konnte nicht sauber beendet werden.")
        for pool in (self.pool,self.tts_pool,self.stt_pool):
            pool.shutdown(wait=False,cancel_futures=True)
