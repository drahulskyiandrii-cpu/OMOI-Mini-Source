"""Konfiguration, lokale Datenbank und sichere, deterministische Werkzeuge."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from .runtime_config import RUNTIME_SETTINGS, validate_runtime_setting

DEFAULT_SETTINGS: dict[str, Any] = {
    **dict.fromkeys(RUNTIME_SETTINGS, ""),
    "llm": "cloud", "stt": "cloud", "tts": "cartesia",
    "stt_profile": "fast", "stt_hotwords": "", "stt_auto_language": False,
    "stt_speech_guard": False, "stt_auto_endpoint": False, "stt_review": False,
    "ui_language": "de", "language": "de", "voice": "Nika", "cartesia_voice_id": "", "microphone_id": "default",
    "piper_voice_de": "de_DE-ramona-low",
    "piper_voice_ru": "ru_RU-irina-medium",
    "speak": True,
    "greeting": True, "always_on_top": True, "mouse_tracking": True,
    "prewarm_local_models": False,
    "avatar": "", "width": 520, "height": 720,
    "qwen_voice_reference": "", "qwen_variant": "base",
    "llm_local_profile": "standard", "llm_gemma12_file": "", "llm_gemma12_server_file": "",
    "llm_model_file": "", "llm_server_file": "",
    "hello_de": "", "hello_ru": "",
    "introduce_de": "", "introduce_ru": "",
    "timer_end_de": "", "timer_end_ru": "",
}
BASE_ANIMATION_SLOTS = {
    "welcome": "omoi-wave-soft.vrma",
    "idle": "omoi-idle-soft-breath.vrma",
    "listening": "omoi-idle-attentive.vrma",
    "talking": "omoi-talk-calm.vrma",
    "explaining": "omoi-talk-explain.vrma",
    "happy": "omoi-excited-small.vrma",
    "laugh": "omoi-laugh-warm.vrma",
    "nod": "omoi-nod-soft.vrma",
    "surprised": "omoi-surprised-soft.vrma",
}
OPTIONAL_ANIMATION_SLOTS = {
    "thinking": "omoi-idle-relaxed.vrma",
    "idle_shift": "omoi-idle-gentle-shift.vrma",
    "sad": "omoi-sad-sigh.vrma",
    "excited": "omoi-excited.vrma",
    "shy": "omoi-look-away-shy.vrma",
    "annoyed": "omoi-annoyed-sigh.vrma",
    "shake_head": "omoi-shake-head-soft.vrma",
    "talk_gentle": "omoi-talk-gentle.vrma",
    "talk_enthusiastic": "omoi-talk-enthusiastic.vrma",
}
ANIMATION_ROLES = {**BASE_ANIMATION_SLOTS, **OPTIONAL_ANIMATION_SLOTS}
ALTERNATIVE_ANIMATION_SLOTS = {
    f"{role}_alt{index}": "" for role in ANIMATION_ROLES for index in (1, 2)
}
KNOWN_SLOTS = {**ANIMATION_ROLES, **ALTERNATIVE_ANIMATION_SLOTS}

def user_data_dir() -> Path:
    base = os.environ.get("OMOI_MINI_DATA_DIR") or str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OMOI-Mini")
    path = Path(base).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path

def legacy_development_enabled() -> bool:
    """Nur bewusst aktivierter Quellcode-Entwicklermodus, niemals EXE."""
    return not getattr(sys,"frozen",False) and os.environ.get("OMOI_MINI_DEV_LEGACY")=="1"


def omoi_model_root() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "aicompanion"

def external_runtime_root() -> Path:
    """Ermittelt eigene STT-/TTS-Worker außerhalb des eingefrorenen GUI-Bundles.

    OMOI_MINI_RUNTIME_ROOT ist ein ausdrücklich gesetztes Benutzerverzeichnis.
    Ohne Angabe erkennt die Portable-EXE einen benachbarten Mini-Checkout.
    Es werden keine unbekannten Programme aus beliebigen Systempfaden gesucht.
    """
    configured = os.environ.get("OMOI_MINI_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents[:6]:
            if ((parent / "local-stt" / "worker.mjs").is_file()
                    or (parent / "local-tts" / "qwen_server.py").is_file()):
                return parent
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]

class Store:
    def __init__(self, database: Path | None = None):
        self.path = database or (user_data_dir() / "omoi-mini.sqlite3")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL").fetchone()
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS stt_corrections(
                    engine TEXT NOT NULL, language TEXT NOT NULL,
                    original TEXT NOT NULL, corrected TEXT NOT NULL,
                    PRIMARY KEY(engine,language,original)
                );
                CREATE TABLE IF NOT EXISTS notes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS animation_assignments(
                    avatar TEXT NOT NULL, slot TEXT NOT NULL, path TEXT NOT NULL,
                    PRIMARY KEY(avatar,slot)
                );
                CREATE TABLE IF NOT EXISTS avatar_favorites(
                    path TEXT PRIMARY KEY, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
    @contextmanager
    def _connect(self):
        db=sqlite3.connect(str(self.path), timeout=.5)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    def get_settings(self) -> dict[str, Any]:
        with self._connect() as db:
            rows=db.execute("SELECT key,value FROM settings").fetchall()
        result=dict(DEFAULT_SETTINGS)
        for key,raw in rows:
            if key not in DEFAULT_SETTINGS:continue
            try:
                value=json.loads(raw)
                self._validate_setting(key,value)
            except (TypeError,ValueError):
                # Eine beschaedigte Anbieterwahl darf nicht still zur Cloud wechseln.
                raise ValueError("Gespeicherte Einstellung ungueltig: "+key) from None
            result[key]=value
        result["llm_mode"]="cloud" if result["llm"]=="cloud" else ("local_12b" if result["llm_local_profile"]=="gemma12" else "local_standard")
        return result

    def set_setting(self, key: str, value: Any) -> None:
        if key=="llm_mode":
            if value not in ("cloud","local_standard","local_12b"):
                raise ValueError("Ungültiges Antwortmodell")
            with self._lock,self._connect() as db:
                pairs=[("llm","cloud" if value=="cloud" else "local")]
                if value!="cloud":
                    pairs.append(("llm_local_profile","gemma12" if value=="local_12b" else "standard"))
                db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[(k,json.dumps(v)) for k,v in pairs])
            return
        self._validate_setting(key,value)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value)))
    @staticmethod
    def _validate_setting(key,value):
        if key not in DEFAULT_SETTINGS:
            raise ValueError("Unbekannte Einstellung")
        if key in RUNTIME_SETTINGS:
            validate_runtime_setting(key, value)
        if key=="microphone_id" and (not isinstance(value,str) or
                (value!="default" and not re.fullmatch(r"[0-9a-f]{64}",value))):
            raise ValueError("Ungültige Mikrofon-ID")
        if key=="cartesia_voice_id" and (not isinstance(value,str) or len(value)>160 or any(c.isspace() or not c.isprintable() for c in value)):
            raise ValueError("Ungültige Cartesia-Voice-ID")
        if key=="qwen_variant" and value not in ("base","cf050"):
            raise ValueError("Unbekannte Qwen-Variante")
        if key=="llm_local_profile" and value not in ("standard","gemma12"):
            raise ValueError("Unbekanntes lokales LLM-Profil")
        if key=="llm" and value not in ("cloud","local"):
            raise ValueError("Ungültiger Anbieter")
        if key=="stt" and value not in ("cloud","local","cuda"):
            raise ValueError("Ungültige Spracherkennung")
        if key=="tts" and value not in ("cartesia","local","piper","none"):
            raise ValueError("Ungültige Sprachausgabe")
        if key=="piper_voice_de" and value not in ("de_DE-eva_k-x_low","de_DE-ramona-low"):
            raise ValueError("Ungültige deutsche Piper-Stimme")
        if key=="piper_voice_ru" and value not in ("ru_RU-irina-medium",):
            raise ValueError("Ungültige russische Piper-Stimme")
        if key=="stt_profile" and value not in ("fast","balanced","quality"):
            raise ValueError("Ungültiges STT-Profil")
        if key=="stt_hotwords" and (not isinstance(value,str) or len(value)>500):
            raise ValueError("STT-Wortliste zu lang")
        if key in ("stt_auto_language","stt_speech_guard","stt_auto_endpoint","stt_review") and type(value) is not bool:
            raise ValueError("Boolean erwartet")
        if key in ("language","ui_language") and value not in ("de","ru"):
            raise ValueError("Ungültige Sprache")
        if key in ("speak","greeting","always_on_top","prewarm_local_models","mouse_tracking") and not isinstance(value,bool):
            raise ValueError("Boolean erwartet")
        if key in ("avatar","voice","qwen_voice_reference",
                   "llm_model_file","llm_server_file","llm_gemma12_file","llm_gemma12_server_file") and (not isinstance(value,str) or len(value)>1024):
            raise ValueError("Ungültiger Dateipfad oder Text")
        if key in ("hello_de","hello_ru","introduce_de","introduce_ru",
                   "timer_end_de","timer_end_ru") and (not isinstance(value,str) or len(value)>1500):
            raise ValueError("Benutzerdefinierter Text ist zu lang")
        if key in ("width","height") and (type(value) is not int or not 200<=value<=4096):
            raise ValueError("Ungueltige Fenstergroesse")
    def phrase(self,name:str,lang:str="de")->str:
        if name not in ("hello","introduce","timer_end") or lang not in ("de","ru"):
            raise ValueError("Unbekannte Textvorlage")
        custom=self.get_settings().get(f"{name}_{lang}","").strip()
        return custom if custom else preset_text(name,lang)

    @staticmethod
    def _correction_key(engine, language, original):
        if (engine not in ("cuda","local") or language not in ("de","ru","en","auto")
                or not isinstance(original,str) or not 0<len(original.strip())<=4000):
            raise ValueError("Invalid STT correction")
        return engine,language,original.strip()

    def stt_suggestion(self, engine, language, original):
        key=self._correction_key(engine,language,original)
        with self._connect() as db:
            row=db.execute("SELECT corrected FROM stt_corrections WHERE engine=? AND language=? AND original=?",key).fetchone()
        return row[0] if row else None

    def remember_stt_correction(self,engine,language,original,corrected):
        key=self._correction_key(engine,language,original)
        if not isinstance(corrected,str) or not 0<len(corrected.strip())<=4000 or corrected.strip()==key[2]:
            raise ValueError("Invalid STT correction")
        with self._lock,self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (db.execute("SELECT COUNT(*) FROM stt_corrections").fetchone()[0]>=1000
                    and not db.execute("SELECT 1 FROM stt_corrections WHERE engine=? AND language=? AND original=?",key).fetchone()):
                raise ValueError("STT correction limit reached")
            db.execute("INSERT INTO stt_corrections VALUES(?,?,?,?) ON CONFLICT(engine,language,original) DO UPDATE SET corrected=excluded.corrected",(*key,corrected.strip()))

    def clear_stt_corrections(self):
        with self._lock,self._connect() as db:
            db.execute("DELETE FROM stt_corrections")

    def add_message(self, role: str, text: str, provider: str="") -> None:
        if role not in ("user","assistant"):
            raise ValueError("Ungültige Rolle")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO messages(role,content,provider) VALUES(?,?,?)",(role,text[:10000],provider[:100]))
    def messages(self, limit: int=10) -> list[dict[str,str]]:
        with self._connect() as db:
            rows = db.execute("SELECT role,content FROM messages ORDER BY id DESC LIMIT ?",(max(1,min(limit,40)),)).fetchall()
        return [{"role":role,"content":text} for role,text in reversed(rows)]
    def clear_messages(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM messages")
    def add_note(self, text: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO notes(content) VALUES(?)",(text[:1500],))
    def notes(self, limit: int=10) -> list[str]:
        with self._connect() as db:
            return [t for (t,) in db.execute("SELECT content FROM notes ORDER BY id DESC LIMIT ?",(limit,))]
    def set_animation(self, avatar: str, slot: str, path: str) -> None:
        if slot not in KNOWN_SLOTS:
            raise ValueError("Unbekannte Animationsrolle")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO animation_assignments VALUES(?,?,?) ON CONFLICT(avatar,slot) DO UPDATE SET path=excluded.path",(avatar,slot,path))
    def reset_animation(self, avatar: str, slot: str) -> None:
        """Entfernt nur die benutzerdefinierte Rollenzuordnung, nie die Originaldatei."""
        if slot not in KNOWN_SLOTS:
            raise ValueError("Unbekannte Animationsrolle")
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM animation_assignments WHERE avatar=? AND slot=?", (avatar, slot))

    def animations(self, avatar: str) -> dict[str,str]:
        with self._connect() as db:
            return dict(db.execute("SELECT slot,path FROM animation_assignments WHERE avatar=?",(avatar,)))

    def favorite_avatars(self) -> list[str]:
        """Nur gespeicherte Pfade, standardmäßig zu Mini-eigenen VRM-Importen."""
        with self._connect() as db:
            return [path for (path,) in db.execute(
                "SELECT path FROM avatar_favorites ORDER BY added_at, rowid"
            )]

    def set_avatar_favorite(self, path: str, approved: bool) -> None:
        """Die Benutzerentscheidung speichern, Quelldateien unverändert lassen."""
        candidate = Path(path)
        if (not isinstance(approved, bool) or len(path) > 1024
                or candidate.suffix.lower() != ".vrm" or candidate.is_symlink()
                or not candidate.is_file() or not 100 <= candidate.stat().st_size < 200 * 1024 * 1024):
            raise ValueError("Ungültiges VRM für die Mini-Demoliste")
        with self._lock, self._connect() as db:
            if approved:
                db.execute(
                    "INSERT OR IGNORE INTO avatar_favorites(path) VALUES(?)",
                    (str(candidate.resolve()),),
                )
            else:
                db.execute("DELETE FROM avatar_favorites WHERE path=?", (str(candidate.resolve()),))

    def other_avatar_animation_count(self, avatar: str) -> int:
        """Zählt nur die Rollen anderer Avatare, niemals Dateinamen übertragen."""
        with self._connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM animation_assignments WHERE avatar!=?", (avatar,)
            ).fetchone()[0])

def classify_preset(text: str) -> str | None:
    normalized = re.sub(r"[?!.,:;]+"," ",text.strip().lower())
    normalized = re.sub(r"\s+"," ",normalized).strip()
    if re.fullmatch(r"(?:kannst du dich (?:bitte )?vorstellen|stell dich (?:bitte )?vor|erzähl(?:e)? (?:mir )?(?:bitte )?etwas über dich|представься(?: пожалуйста)?)",normalized):
        return "introduce"
    if re.fullmatch(r"(?:hallo(?: omoi)?|guten morgen|привет(?: омои)?)",normalized):
        return "hello"
    return None

INTRO_DE = ("Natürlich! Ich bin OMOI Mini, eine kleine KI-Assistentin, die mit Python entwickelt wurde. "
            "Du kannst mit mir sprechen oder schreiben, Deutsch mit mir üben und einfache Aufgaben erledigen. "
            "Außerdem kannst du zwischen einer lokalen KI und einer Cloud-KI wechseln.")
HELLO_DE = "Hallo! Schön, dass du da bist. Ich bin OMOI Mini."
INTRO_RU = "Конечно! Я OMOI Mini, небольшая ИИ-помощница. Со мной можно разговаривать и переписываться, изучать немецкий и пользоваться простыми инструментами."
HELLO_RU = "Привет! Я OMOI Mini. О чём поговорим?"

def preset_text(name: str, lang: str="de") -> str:
    return {"introduce": INTRO_RU if lang=="ru" else INTRO_DE,
            "hello": HELLO_RU if lang=="ru" else HELLO_DE,
            "timer_end": "Время вышло!" if lang=="ru" else "Die Zeit ist um!"}[name]

_STT_ASSISTANT_NAME = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9])(?:omoi|omae|omai|омои|омай|у[ ]+мой|умой)[ ]+(?:mini|мини)(?![A-Za-zА-Яа-я0-9])",
    re.IGNORECASE,
)

def normalize_stt_text(text: str) -> str:
    """Korrigiert ausschließlich bekannte Transkriptionsvarianten des Eigennamens."""
    return _STT_ASSISTANT_NAME.sub("OMOI Mini", text)

def infer_emotion(text: str) -> str:
    """Bewusst konservative Emotionsklassifizierung ohne zusätzlichen LLM-Aufruf."""
    prefix=text.strip().lower()[:70]
    if prefix.startswith(("haha","hahaha","das ist lustig","хаха")):return "amused"
    if prefix.startswith(("oh nein","das tut mir leid","к сожалению","мне жаль")):return "sad"
    if prefix.startswith(("wow","unglaublich!","вау")):return "excited"
    if prefix.startswith(("hallo","super!","das freut mich","wunderbar","привет")):return "happy"
    return "neutral"

def deterministic_action(text: str, store: Store, lang: str="de") -> tuple[str,str] | None:
    t=text.strip()
    low=t.lower()
    if re.search(r"\b(wie spät ist es|wie viel uhr ist es|сколько времени)\b",low):
        now=datetime.now().strftime("%H:%M")
        return ("time",f"Es ist {now} Uhr." if lang=="de" else f"Сейчас {now}.")
    m=re.match(r"^(?:notiere(?: bitte)?(?:,)?(?: dass)?|schreib(?:e)? (?:bitte )?(?:eine )?notiz(?:,)?(?: dass)?|запиши(?: заметку)?(?:,)?(?: что)?)\s+(.+)$",t,re.I)
    if m:
        note=m.group(1).strip()
        if not note:
            return ("note","Bitte nenne den Inhalt der Notiz.")
        store.add_note(note)
        return ("note",("Notiz gespeichert: " if lang=="de" else "Заметка сохранена: ")+note)
    if re.search(r"(was habe ich mir notiert|zeig(?:e)? (?:mir )?(?:die )?notizen|мои заметки|что я записал)",low):
        notes=store.notes()
        if not notes: return ("notes","Noch keine Notizen." if lang=="de" else "Пока заметок нет.")
        return ("notes",("Deine Notizen: " if lang=="de" else "Твои заметки: ")+"; ".join(notes))
    return None

def resolve_avatar(preferred: str, favorites: list[str] | None = None) -> Path | None:
    """Nur explizite Auswahl, niemals zufällig erstes Bibliotheksmodell.

    Existieren Mini-Favoriten, wird nur ein solcher VRM automatisch geladen.
    Ohne Favoriten bleibt eine zuvor bewusst gespeicherte Auswahl erhalten.
    """
    def valid(raw: str) -> Path | None:
        if not raw:
            return None
        path = Path(raw)
        try:
            if (path.is_file() and not path.is_symlink()
                    and path.suffix.lower() == ".vrm"
                    and 100 <= path.stat().st_size < 200 * 1024 * 1024):
                return path.resolve()
        except OSError:
            pass
        return None

    selected = valid(preferred)
    approved = [candidate for raw in (favorites or [])
                if (candidate := valid(raw)) is not None]
    if approved:
        return selected if selected in approved else approved[0]
    return selected

def resolve_animation(slot: str, avatar: str, store: Store) -> Path | None:
    if slot not in KNOWN_SLOTS: return None
    saved=store.animations(avatar).get(slot)
    if saved:
        path=Path(saved)
        if path.is_file() and path.suffix.lower()==".vrma" and path.stat().st_size<128*1024*1024:
            return path
    # Benutzerdefinierte, lokal freigegebene Animationsstandards.
    default=user_data_dir()/"default-animations"/(slot+".vrma")
    if default.is_file() and not default.is_symlink():
        return default
    # Explicit local preview packs can omit alternatives without falling back
    # to unrelated bundled motions. Normal profiles retain the old fallback.
    if os.environ.get("OMOI_MINI_LOCAL_ANIMATIONS_ONLY") == "1":
        return None
    # Für eine öffentliche Version dürfen nur nach Rechteprüfung autorisierte
    # Assets im eigenständigen Mini-Paket enthalten sein.
    bundle_root=Path(getattr(sys,"_MEIPASS",external_runtime_root()))
    bundled=bundle_root/"assets"/"default-animations"/(slot+".vrma")
    return bundled if bundled.is_file() and not bundled.is_symlink() else None
