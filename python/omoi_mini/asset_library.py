"""Eigenständige Mini-Assetbibliothek, ohne Abhängigkeit vom großen OMOI.

Private VRM/VRMA-Dateien werden ausschließlich in das Benutzerprofil importiert.
Ungeklärte Nutzungsrechte berechtigen nicht zum Git-/Installer-Redistribution.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
from pathlib import Path

from .animation_assets import validate_vrma
from .core import KNOWN_SLOTS, Store, user_data_dir

MAX_VRM_BYTES = 200 * 1024 * 1024
MAX_META_BYTES = 8 * 1024 * 1024


class InvalidAvatar(ValueError):
    """Keine reguläre, sicher lesbare VRM-Datei."""


def validate_vrm(source: str | Path) -> None:
    path = Path(source)
    if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".vrm":
        raise InvalidAvatar("Bitte eine reguläre .vrm-Datei wählen.")
    size = path.stat().st_size
    if not 100 <= size <= MAX_VRM_BYTES:
        raise InvalidAvatar("VRM-Dateigröße ungültig.")
    try:
        with path.open("rb") as fp:
            header = fp.read(20)
            if len(header) != 20:
                raise InvalidAvatar("VRM-Header fehlt.")
            magic, version, total, count, chunk = struct.unpack("<4sIII4s", header)
            if magic != b"glTF" or version != 2 or total != size:
                raise InvalidAvatar("Kein gültiger GLB-2.0-Container.")
            if chunk != b"JSON" or not 2 <= count <= MAX_META_BYTES or 20 + count > total:
                raise InvalidAvatar("VRM-Metadaten beschädigt.")
            data = json.loads(fp.read(count).decode("utf-8"))
        extensions = data.get("extensions", {})
        if (not isinstance(extensions, dict) or
                not ("VRMC_vrm" in extensions or "VRM" in extensions)):
            raise InvalidAvatar("VRM-Avatar-Erweiterung fehlt.")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, struct.error, RecursionError) as exc:
        if isinstance(exc, InvalidAvatar):
            raise
        raise InvalidAvatar("VRM kann nicht geprüft werden.") from None


def _copy_atomic(source: Path, destination: Path, validate) -> Path:
    digest = hashlib.sha256()
    with source.open("rb") as fp:
        while part := fp.read(1024 * 1024):
            digest.update(part)
    location = destination / (digest.hexdigest() + source.suffix.lower())
    destination.mkdir(parents=True, exist_ok=True)
    if not location.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=destination, suffix=source.suffix, delete=False) as out:
                temporary = Path(out.name)
                with source.open("rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
            validate(temporary)
            copied_hash = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if copied_hash != digest.hexdigest():
                raise ValueError("Datei wurde während des Imports geändert.")
            os.replace(temporary, location)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    validate(location)
    return location


def import_vrm(source: str | Path) -> Path:
    """Privaten Avatar in Mini-Profil aufnehmen, Quelle unverändert lassen."""
    path = Path(source)
    validate_vrm(path)
    return _copy_atomic(path, user_data_dir() / "avatars", validate_vrm)


def installed_mini_avatars() -> list[Path]:
    library = user_data_dir() / "avatars"
    results: list[Path] = []
    if library.is_dir():
        for file in sorted(library.glob("*.vrm"))[:100]:
            try:
                validate_vrm(file)
                results.append(file.resolve())
            except (OSError, InvalidAvatar):
                continue
    return results


def save_selected_animation_defaults(store: Store, avatar: str) -> dict[str, Path]:
    """Die bereits je Avatar bestätigten VRMA als *lokale* Mini-Standards übernehmen.

    Diese Funktion verteilt nichts über GitHub. Für einen öffentlichen
    Installer ist für jede VRMA-Datei die separate Umverteilungserlaubnis nötig.
    """
    selected = store.animations(avatar)
    if not selected:
        raise ValueError("Keine bestätigten Animationen am aktuellen Avatar.")
    chosen: dict[str, Path] = {}
    for slot, raw in selected.items():
        if slot not in KNOWN_SLOTS:
            continue
        path = Path(raw)
        validate_vrma(path)
        chosen[slot] = path
    if not chosen:
        raise ValueError("Keine gültigen VRMA-Dateien ausgewählt.")
    destination = user_data_dir() / "default-animations"
    destination.mkdir(parents=True, exist_ok=True)
    imported: dict[str, Path] = {}
    for slot, source in chosen.items():
        fixed = destination / f"{slot}.vrma"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=destination,
                                             suffix=".vrma", delete=False) as out:
                temporary = Path(out.name)
                with source.open("rb") as original:
                    shutil.copyfileobj(original, out, 1024 * 1024)
            validate_vrma(temporary)
            with temporary.open("rb") as copied, source.open("rb") as original:
                def digest(fp):
                    state=hashlib.sha256()
                    while part := fp.read(1024 * 1024):
                        state.update(part)
                    return state.digest()
                if digest(copied) != digest(original):
                    raise ValueError("Animation beim Kopieren verändert.")
            os.replace(temporary, fixed)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        imported[slot] = fixed
    # Nur lokale private Metadaten; keine Pfade oder Assets ins öffentliche Repo.
    manifest = {
        "schema": 1,
        "roles": {slot: hashlib.sha256(path.read_bytes()).hexdigest()
                  for slot, path in imported.items()},
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return imported
