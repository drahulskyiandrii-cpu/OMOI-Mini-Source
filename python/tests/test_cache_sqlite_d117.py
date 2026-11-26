"""Dateicache und echte SQLite-Parallelitaet, ohne Inferenz."""
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from omoi_mini.audio_cache import prune_cache,touch_cached
from omoi_mini.core import Store


def test_cache_prunes_oldest_and_preserves_recent_protected_and_foreign(tmp_path):
    now=2000
    def cache(i,stamp):
        p=tmp_path/(f"{i:064x}"+".wav")
        p.write_bytes(b"x"*10)
        os.utime(p,(stamp,stamp))
        return p
    first=cache(1,1)
    second=cache(2,2)
    protected=cache(3,3)
    fresh=cache(4,now-10)
    foreign=tmp_path/"personal.wav"
    foreign.write_bytes(b"private")
    prune_cache(tmp_path,{protected},max_bytes=20,max_files=2,now=now,grace=300)
    assert not first.exists() and not second.exists()
    assert protected.exists() and fresh.exists() and foreign.read_bytes()==b"private"


def test_cache_touch_preserves_recently_used(tmp_path):
    p=tmp_path/("a"*64+".wav")
    p.write_bytes(b"test")
    os.utime(p,(1,1))
    touch_cached(p)
    prune_cache(tmp_path,max_bytes=0,max_files=0)
    assert p.exists()


def test_cache_handles_locked_file(tmp_path,monkeypatch):
    from pathlib import Path
    p=tmp_path/("a"*64+".wav");p.write_bytes(b"test")
    original=Path.unlink
    def locked(self,*a,**kw):
        if self==p:raise PermissionError("busy")
        return original(self,*a,**kw)
    monkeypatch.setattr(Path,"unlink",locked)
    prune_cache(tmp_path,max_files=0,grace=0)
    assert p.exists()


def test_wal_reads_while_other_connection_writes(tmp_path):
    store=Store(tmp_path/"db")
    store.set_setting("llm_mode","local_standard")
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0]=="wal"
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE settings SET value='invalid' WHERE key='llm'")
        started=time.monotonic()
        assert store.get_settings()["llm_mode"]=="local_standard"
        assert time.monotonic()-started<.5
        db.rollback()
    assert store.get_settings()["llm_mode"]=="local_standard"


def test_busy_writer_fails_instead_of_freezing_for_five_seconds(tmp_path):
    store=Store(tmp_path/"db")
    with sqlite3.connect(store.path) as db:
        db.execute("BEGIN IMMEDIATE")
        start=time.monotonic()
        with pytest.raises(sqlite3.OperationalError):store.add_note("test")
        assert time.monotonic()-start<2
        db.rollback()
    store.add_note("recovered")
    assert store.notes()==["recovered"]


def test_concurrent_writes_and_atomic_profile_snapshots(tmp_path):
    path=tmp_path/"db"
    stores=[Store(path) for _ in range(4)]
    gate=Barrier(4)
    def worker(i):
        store=stores[i]
        gate.wait(3)
        for j in range(40):
            store.set_setting("llm_mode","local_12b" if j%2 else "local_standard")
            store.add_message("user",f"{i}:{j}")
            settings=store.get_settings()
            assert settings["llm"]=="local"
            assert settings["llm_mode"]==("local_12b" if settings["llm_local_profile"]=="gemma12" else "local_standard")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker,range(4)))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(DISTINCT content) FROM messages").fetchone()[0]==160
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_correction_limit_is_atomic_between_store_instances(tmp_path):
    path=tmp_path/"db"
    stores=[Store(path),Store(path)]
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO stt_corrections VALUES('cuda','de',?,?)",
                       [(str(i),"corrected") for i in range(999)])
    gate=Barrier(2)
    def add(i):
        gate.wait(3)
        try:
            stores[i].remember_stt_correction("cuda","de","new"+str(i),"fixed")
            return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(add,range(2)))==[False,True]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM stt_corrections").fetchone()[0]==1000


@pytest.mark.parametrize("value",["{bad",'"unknown"',"null","true"])
def test_corrupt_provider_never_falls_back_to_cloud(tmp_path,value):
    store=Store(tmp_path/"db")
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO settings VALUES('llm',?)",(value,))
    with pytest.raises(ValueError,match="llm"):store.get_settings()
    store.set_setting("llm","local")
    assert store.get_settings()["llm"]=="local"


def test_unknown_legacy_json_does_not_break_settings(tmp_path):
    store=Store(tmp_path/"db")
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO settings VALUES('obsolete','{bad')")
    assert store.get_settings()["stt"]=="cloud"


@pytest.mark.parametrize("value",[True,None,"520",-1,999999])
def test_window_dimensions_validated(tmp_path,value):
    store=Store(tmp_path/"db")
    with pytest.raises(ValueError):store.set_setting("width",value)


def test_cache_maintenance_preserves_current_phrase_library(tmp_path,monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from omoi_mini.controller import Controller
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    ctrl=Controller(store)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    calls=[]
    monkeypatch.setattr("omoi_mini.controller.prune_cache",lambda root,protected:calls.append((root,protected)))
    try:
        ctrl.maintain_audio_cache()
        assert len(calls)==1
        root,protected=calls[0]
        assert root==tmp_path/"audio-cache"
        for lang in ("de","ru"):
            for name in ("hello","introduce","timer_end"):
                assert ctrl._cache_path(store.phrase(name,lang),lang,"cartesia") in protected
        monkeypatch.setattr(ctrl,"voice_busy",lambda:True)
        ctrl.maintain_audio_cache()
        assert len(calls)==1
    finally:ctrl.close()


def test_disabled_speech_cannot_prepare_cloud_phrases(tmp_path,monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from omoi_mini.controller import Controller
    QCoreApplication.instance() or QCoreApplication([])
    store=Store(tmp_path/"db")
    store.set_setting("speak",False)
    ctrl=Controller(store)
    monkeypatch.setattr("omoi_mini.controller.user_data_dir",lambda:tmp_path)
    monkeypatch.setattr("omoi_mini.controller.cartesia_wav",lambda *a,**kw:pytest.fail("disabled cloud synthesis"))
    try:ctrl._prepare_phrases_worker()
    finally:ctrl.close()
