"""Begrenzter regenerierbarer WAV-Cache; keine privaten Quelldateien loeschen."""
from __future__ import annotations
import os
import re
import stat
import time
import wave
from .pcm_wave import read_pcm_wav
from pathlib import Path

CACHE_BYTES=256*1024*1024
CACHE_FILES=512
CACHE_GRACE_SECONDS=300
_CACHE_NAME=re.compile(r"[0-9a-f]{64}\.wav\Z")


def _plain_path(path:Path,kind):
    info=path.lstat()
    return (kind(info.st_mode) and not path.is_symlink()
            and not getattr(info,"st_file_attributes",0)&stat.FILE_ATTRIBUTE_REPARSE_POINT)


def prepare_cache_target(path:Path):
    """Nur regulaere Dateien im eigenen, nicht umgeleiteten Cache ersetzen."""
    if not _CACHE_NAME.fullmatch(path.name):
        raise OSError("Invalid cache filename")
    path.parent.mkdir(exist_ok=True)
    if not _plain_path(path.parent,stat.S_ISDIR):
        raise OSError("Invalid cache directory")
    try:
        if not _plain_path(path,stat.S_ISREG):
            raise OSError("Invalid cache entry")
    except FileNotFoundError:
        pass


def cache_ready(path:Path)->bool:
    """Beschaedigte, verkuerzte oder umgeleitete WAVs nicht wiederverwenden."""
    try:
        if (not _CACHE_NAME.fullmatch(path.name)
                or not _plain_path(path.parent,stat.S_ISDIR)
                or not _plain_path(path,stat.S_ISREG)
                or not 44<=path.stat().st_size<=32*1024*1024):
            return False
        read_pcm_wav(str(path),channels=(1,))
        return True
    except (OSError,ValueError,EOFError,wave.Error):return False


def touch_cached(path:Path):
    try:
        if _plain_path(path.parent,stat.S_ISDIR) and _plain_path(path,stat.S_ISREG):
            if path.stat().st_nlink==1:os.utime(path,None)
    except OSError:pass


def prune_cache(root:Path,protected=(),*,max_bytes=CACHE_BYTES,
                max_files=CACHE_FILES,grace=CACHE_GRACE_SECONDS,now=None):
    """Alte Cache-Dateien zuerst; frische, geschuetzte und fremde Dateien behalten."""
    try:
        if not _plain_path(root,stat.S_ISDIR):return
    except OSError:return
    now=time.time() if now is None else now
    protected=set(protected)
    entries=[]
    try:
        for path in root.iterdir():
            try:
                if not _CACHE_NAME.fullmatch(path.name) or not _plain_path(path,stat.S_ISREG):continue
                info=path.stat()
                entries.append((info.st_mtime,info.st_size,path))
            except OSError:continue
    except OSError:return
    total=sum(size for _,size,_ in entries)
    count=len(entries)
    for stamp,size,path in sorted(entries):
        if total<=max_bytes and count<=max_files:break
        if path in protected or now-stamp<grace:continue
        try:
            # Eine zwischenzeitlich erneuerte Datei nicht entfernen.
            if not _plain_path(path,stat.S_ISREG) or path.stat().st_mtime!=stamp:continue
            path.unlink()
        except OSError:continue
        total-=size
        count-=1
