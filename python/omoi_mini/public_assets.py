"""Strenge technische Freigabesperre für verteilbare Mini-Standardanimationen.

Ein bestandener Manifest-Check bestätigt NICHT die tatsächliche Rechtslage.
Die Rechte/Quellen jedes Assets müssen vor öffentlicher Ausgabe human geprüft sein.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .animation_assets import InvalidAnimation, validate_vrma
from .core import BASE_ANIMATION_SLOTS, KNOWN_SLOTS

EXPECTED_FILES = frozenset(f"{slot}.vrma" for slot in KNOWN_SLOTS)
# Laugh and surprise may be represented by facial expression and base motion.
REQUIRED_FILES = frozenset(f"{slot}.vrma" for slot in BASE_ANIMATION_SLOTS
                           if slot not in {"laugh", "surprised"})
MAX_LICENSE_BYTES = 128 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PublicAssetError(ValueError):
    """Automatische Weitergabe ohne technische Lizenzfreigabe verboten."""


def validate_public_animations(directory: str | Path) -> list[Path]:
    """Prüft nur Assets eines vollständigen Pakets mit definierten Pflichtrollen und optionalen bekannten Rollen.

    Keine VRMA: bestehendes leeres, zulässiges Developer-Build.
    Mindestens eine VRMA: benannte Pflichtrollen + LICENSES.json mit
    Hash, Attribution, Herkunft und explizit bestätigter Redistribution.
    """
    folder = Path(directory)
    if folder.is_symlink():
        raise PublicAssetError("Symbolische Links als Assetordner sind verboten")
    if not folder.is_dir():
        return []
    # PyInstaller würde den gesamten Ordner einpacken: unbekannte Dateien
    # (z. B. private WAV, Tokens, Nutzer-SQLite) unbedingt vorher sperren.
    allowed = EXPECTED_FILES | {"README.md", "LICENSES.json", "LICENSES.example.json", "NOTICE.md", "LICENSE-Apache-2.0.txt"}
    for file in folder.iterdir():
        if file.name not in allowed or not file.is_file() or file.is_symlink():
            raise PublicAssetError("Unbekannte oder verlinkte Datei im öffentlichen Assetordner")
    clips = sorted(folder.glob("*.vrma"))
    manifest = folder / "LICENSES.json"
    if not clips:
        if manifest.exists() or manifest.is_symlink():
            raise PublicAssetError("Verwaistes Animations-Lizenzmanifest")
        return []
    clip_names = {path.name for path in clips}
    if not REQUIRED_FILES <= clip_names <= EXPECTED_FILES:
        raise PublicAssetError("Öffentliches Animationspaket benötigt alle definierten VRMA-Pflichtrollen")
    if (not manifest.is_file() or manifest.is_symlink()
            or not 2 <= manifest.stat().st_size <= MAX_LICENSE_BYTES):
        raise PublicAssetError("LICENSES.json fehlt oder ist ungültig")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PublicAssetError("LICENSES.json ist nicht lesbar") from None
    if (not isinstance(document, dict) or document.get("schema") != 1
            or set(document) != {"schema", "files"}):
        raise PublicAssetError("LICENSES.json: exakt Schema 1 und Dateiliste erforderlich")
    entries = document.get("files")
    if not isinstance(entries, list) or len(entries) != len(clip_names):
        raise PublicAssetError("Lizenznachweis für jede enthaltene Rolle erforderlich")
    by_name: dict[str, dict] = {}
    permitted_fields = {"name", "license", "author", "source_url",
                        "sha256", "redistribution_allowed"}
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != permitted_fields
                or not isinstance(entry.get("name"), str)):
            raise PublicAssetError("Lizenz-Eintrag enthält fehlende oder unerwartete Felder")
        name = entry["name"]
        if name not in clip_names or name in by_name:
            raise PublicAssetError("Lizenzmanifest enthält unbekannte oder doppelte Rolle")
        license_id = entry.get("license")
        author = entry.get("author")
        source = entry.get("source_url")
        digest = entry.get("sha256")
        if (not isinstance(license_id, str) or not 2 <= len(license_id.strip()) <= 100
                or license_id.strip().lower() in ("unknown", "private", "proprietary")):
            raise PublicAssetError("Konkrete Lizenz pro VRMA erforderlich")
        if not isinstance(author, str) or not 2 <= len(author.strip()) <= 200:
            raise PublicAssetError("Urheber-/Attributionsangabe erforderlich")
        if not isinstance(source, str) or len(source) > 2048:
            raise PublicAssetError("Quellen-URL erforderlich")
        try:
            url = urlparse(source)
            valid_url = (url.scheme == "https" and bool(url.hostname)
                         and not url.username and not url.password
                         and not url.query and not url.fragment)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise PublicAssetError("Quellen-URL muss öffentliche HTTPS-Adresse ohne Zugangsdaten sein")
        if entry.get("redistribution_allowed") is not True:
            raise PublicAssetError("Weitergabe nicht ausdrücklich bestätigt")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise PublicAssetError("SHA-256-Prüfsumme fehlt oder ist ungültig")
        by_name[name] = entry
    if any(entry["license"] == "Apache-2.0" for entry in by_name.values()):
        for name in ("NOTICE.md", "LICENSE-Apache-2.0.txt"):
            file = folder / name
            if not file.is_file() or file.is_symlink() or not 1 <= file.stat().st_size <= MAX_LICENSE_BYTES:
                raise PublicAssetError("Apache-2.0-Lizenztext oder NOTICE fehlt")
    for clip in clips:
        if clip.is_symlink() or not clip.is_file():
            raise PublicAssetError("Symlink oder ungültige Animationsdatei")
        try:
            validate_vrma(clip)
        except (OSError, InvalidAnimation) as exc:
            raise PublicAssetError("VRMA-Strukturprüfung fehlgeschlagen") from None
        hash_state = hashlib.sha256()
        with clip.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hash_state.update(chunk)
        if hash_state.hexdigest() != by_name[clip.name]["sha256"]:
            raise PublicAssetError("Manifest-Hash stimmt nicht mit VRMA überein")
    return clips
