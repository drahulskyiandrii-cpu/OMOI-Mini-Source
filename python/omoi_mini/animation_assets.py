"""VRMA-Dateien vor einer Zuordnung sicher prüfen und lokal archivieren.

Diese Prüfung validiert GLB-Struktur und VRMC_vrm_animation-Metadaten,
nicht die anatomische Retargeting-Qualität auf einem bestimmten VRM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_VRMA_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024


class InvalidAnimation(ValueError):
    """Datei ist keine verwendbare VRM-Animation."""


@dataclass(frozen=True)
class AnimationInfo:
    path: Path
    original_name: str
    sha256: str
    animation_count: int
    channel_count: int


def validate_vrma(source: str | Path) -> tuple[int, int]:
    """Prüft Dateigröße, GLB-Header, JSON-Chunk und VRMA-Extension."""
    path = Path(source)
    if path.suffix.lower() != ".vrma" or not path.is_file() or path.is_symlink():
        raise InvalidAnimation("Bitte eine reguläre VRMA-Datei auswählen.")
    size = path.stat().st_size
    if size < 20 or size > MAX_VRMA_BYTES:
        raise InvalidAnimation("Die VRMA-Dateigröße ist ungültig.")
    try:
        with path.open("rb") as file:
            header = file.read(12)
            if len(header) != 12:
                raise InvalidAnimation("GLB-Header fehlt.")
            magic, version, total_size = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or total_size != size:
                raise InvalidAnimation("Ungültiger glTF-2.0-Container.")
            json_header = file.read(8)
            if len(json_header) != 8:
                raise InvalidAnimation("JSON-Chunk fehlt.")
            json_size, kind = struct.unpack("<I4s", json_header)
            if kind != b"JSON" or not 2 <= json_size <= MAX_JSON_BYTES:
                raise InvalidAnimation("Ungültiger JSON-Chunk.")
            if json_size % 4 != 0 or 20 + json_size > size:
                raise InvalidAnimation("Beschädigte GLB-Längenangaben.")
            document = json.loads(file.read(json_size).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error, RecursionError):
        raise InvalidAnimation("VRMA kann nicht gelesen werden.") from None

    if (not isinstance(document, dict) or not isinstance(document.get("asset"), dict)
            or document["asset"].get("version") != "2.0"):
        raise InvalidAnimation("Ungültige glTF-2.0-Metadaten.")
    extensions = document.get("extensions", {})
    if not isinstance(extensions, dict) or not isinstance(extensions.get("VRMC_vrm_animation"), dict):
        raise InvalidAnimation("VRMC_vrm_animation-Extension fehlt.")
    animations = document.get("animations")
    if not isinstance(animations, list) or not animations:
        raise InvalidAnimation("Keine Animation in der Datei vorhanden.")
    channels = 0
    for animation in animations:
        if not isinstance(animation, dict):
            raise InvalidAnimation("Animationsdaten sind beschädigt.")
        tracks = animation.get("channels")
        samplers = animation.get("samplers")
        if not isinstance(tracks, list) or not tracks or not isinstance(samplers, list) or not samplers:
            raise InvalidAnimation("Animationskanäle oder Sampler fehlen.")
        for track in tracks:
            if (not isinstance(track, dict) or not isinstance(track.get("sampler"), int)
                    or not 0 <= track["sampler"] < len(samplers)):
                raise InvalidAnimation("Ungültiger Animations-Sampler.")
        channels += len(tracks)
    return len(animations), channels


def import_vrma(source: str | Path, destination: Path) -> AnimationInfo:
    """Kopiert geprüfte VRMA-Assets unverändert in ein lokales Benutzerverzeichnis.

    Quell-Datei bleibt unberührt. Keine Aufnahme von Assets in das Git-Repository.
    """
    source_path = Path(source)
    animation_count, channel_count = validate_vrma(source_path)
    digest = hashlib.sha256()
    with source_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    destination.mkdir(parents=True, exist_ok=True)
    copied = destination / (digest.hexdigest() + ".vrma")
    if not copied.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=destination, suffix=".vrma",
                                             delete=False) as target:
                temporary = Path(target.name)
                with source_path.open("rb") as original:
                    shutil.copyfileobj(original, target, 1024 * 1024)
            # Verhindert, dass eine zwischenzeitlich veränderte Quelle archiviert wird.
            copied_hash = hashlib.sha256()
            with temporary.open("rb") as staged:
                while chunk := staged.read(1024 * 1024):
                    copied_hash.update(chunk)
            if copied_hash.hexdigest() != digest.hexdigest():
                raise InvalidAnimation("Die Quelldatei wurde während des Imports verändert.")
            validate_vrma(temporary)
            os.replace(temporary, copied)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    # Auch vorhandene Cache-Einträge werden auf Beschädigung geprüft.
    validate_vrma(copied)
    return AnimationInfo(copied, source_path.name, digest.hexdigest(),
                         animation_count, channel_count)
