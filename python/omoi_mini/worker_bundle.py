"""Reproduzierbare, offline verifizierte Quell-Worker-Pakete für OMOI Mini.

Keine Modelle, venvs, npm node_modules, Audiodateien oder API-Credentials.
Dieses Modul installiert KEINE Python-/Node-Abhängigkeiten und startet nichts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path

FILES = (
    "local-stt/worker.mjs",
    "local-stt/cuda_worker.py",
    "local-stt/package.json",
    "local-stt/package-lock.json",
    "local-tts/qwen_server.py",
    "local-tts/piper_worker.py",
)
MANIFEST_NAME = "WORKER_BUNDLE_MANIFEST.json"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class WorkerBundleError(ValueError):
    """Eine Worker-Paket-Integritäts-/Sicherheitsbedingung ist nicht erfüllt."""


def _regular_file(path: Path, *, label: str) -> bytes:
    if (not path.is_file() or path.is_symlink()
            or not 0 < path.stat().st_size <= MAX_FILE_BYTES):
        raise WorkerBundleError(f"Fehlende, verlinkte oder zu große Datei: {label}")
    payload = path.read_bytes()
    if not 0 < len(payload) <= MAX_FILE_BYTES:
        raise WorkerBundleError(f"Unzulässige Größe: {label}")
    return payload


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_worker_bundle(source: Path, destination: Path) -> dict:
    """Baut NUR exakt benannte Quellskripte; überschreibt keine ZIP-Datei."""
    source, destination = Path(source), Path(destination)
    if not source.is_dir() or source.is_symlink():
        raise WorkerBundleError("Quellverzeichnis fehlt oder ist verlinkt")
    if destination.exists() or destination.is_symlink():
        raise WorkerBundleError("Zieldatei existiert bereits")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise WorkerBundleError("Zielordner fehlt oder ist verlinkt")
    if source.resolve() == destination.parent.resolve():
        raise WorkerBundleError("Worker-Paket nicht direkt im Source-Root ablegen")

    payloads = {}
    for name in FILES:
        candidate = source / name
        # Auch Symlink-Verzeichnisse unterhalb des Checkouts strikt sperren.
        if (candidate.parent.is_symlink()
                or not candidate.resolve().is_relative_to(source.resolve())):
            raise WorkerBundleError("Verlinktes Worker-Quellverzeichnis verboten")
        payloads[name] = _regular_file(candidate, label=name)
    entries = [
        {"name": name, "size": len(payloads[name]),
         "sha256": hashlib.sha256(payloads[name]).hexdigest()}
        for name in FILES
    ]
    manifest = {"schema": 1, "kind": "omoi-mini-worker-sources",
                "files": entries}
    stage = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, suffix=".zip", delete=False
        ) as fd:
            stage = Path(fd.name)
        with zipfile.ZipFile(stage, "w", allowZip64=False) as archive:
            for name in FILES:
                archive.writestr(_zip_info(name), payloads[name])
            archive.writestr(
                _zip_info(MANIFEST_NAME),
                (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n").encode("utf-8"),
            )
        if stage.stat().st_size > MAX_ARCHIVE_BYTES:
            raise WorkerBundleError("Worker-Paket ist zu groß")
        verified = verify_worker_bundle(stage)
        # os.link ist eine atomare No-Clobber-Erstellung auf demselben Volume:
        # ein inzwischen angelegtes Ziel wird niemals überschrieben.
        os.link(stage, destination)
        return verified
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def _verified_payloads(bundle: Path) -> dict[str, bytes]:
    """Liest und prüft alle Bytes einmal; spätere Kopie nutzt exakt diese Bytes."""
    bundle = Path(bundle)
    if (not bundle.is_file() or bundle.is_symlink()
            or not 0 < bundle.stat().st_size <= MAX_ARCHIVE_BYTES):
        raise WorkerBundleError("Worker-Paket fehlt, ist verlinkt oder zu groß")
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            infos = archive.infolist()
            expected = set(FILES) | {MANIFEST_NAME}
            if (len(infos) != len(expected)
                    or {i.filename for i in infos} != expected):
                raise WorkerBundleError("Unerwartete oder doppelte ZIP-Einträge")
            for info in infos:
                if (info.is_dir() or info.flag_bits & 1
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.file_size <= 0
                        or info.file_size > MAX_FILE_BYTES):
                    raise WorkerBundleError("Ungültiger ZIP-Eintrag")
            raw_manifest = archive.read(MANIFEST_NAME)
            data = json.loads(raw_manifest)
            if (not isinstance(data, dict) or set(data) != {"schema", "kind", "files"}
                    or data["schema"] != 1
                    or data["kind"] != "omoi-mini-worker-sources"):
                raise WorkerBundleError("Manifesttyp ungültig")
            entries = data["files"]
            if not isinstance(entries, list) or len(entries) != len(FILES):
                raise WorkerBundleError("Unvollständiges Manifest")
            by_name = {}
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"name", "size", "sha256"}:
                    raise WorkerBundleError("Manifestfelder ungültig")
                name = entry["name"]
                if (not isinstance(name, str) or name not in FILES or name in by_name
                        or type(entry["size"]) is not int
                        or not 0 < entry["size"] <= MAX_FILE_BYTES
                        or not isinstance(entry["sha256"], str)
                        or not _HEX.fullmatch(entry["sha256"])):
                    raise WorkerBundleError("Manifesteintrag ungültig")
                by_name[name] = entry
            if set(by_name) != set(FILES):
                raise WorkerBundleError("Dateiliste unvollständig")
            payloads = {}
            for name in FILES:
                info = archive.getinfo(name)
                if info.file_size != by_name[name]["size"]:
                    raise WorkerBundleError("Dateilänge abweichend")
                content = archive.read(name)
                if hashlib.sha256(content).hexdigest() != by_name[name]["sha256"]:
                    raise WorkerBundleError("Datei-Hash stimmt nicht")
                payloads[name] = content
    except WorkerBundleError:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
            RuntimeError, EOFError):
        raise WorkerBundleError("Worker-Paket ist beschädigt") from None
    return payloads


def verify_worker_bundle(bundle: Path) -> dict:
    """Prüft ZIP-Struktur, Manifest und jeden Hash vor jeder Extraktion."""
    payloads = _verified_payloads(bundle)
    return {"schema": 1, "file_count": len(payloads), "verified": True}


def install_worker_bundle(
    bundle: Path, target: Path, *, apply: bool = False
) -> dict:
    """Dry-run ist Standard; --apply legt NUR ein neues, leeres Ziel an.

    Führt keine der Dateien aus, lädt keine Modelle und verändert keine
    bereits vorhandenen Mini-/Haupt-OMOI-Verzeichnisse.
    """
    payloads = _verified_payloads(bundle)
    result = {"schema": 1, "file_count": len(payloads), "verified": True}
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise WorkerBundleError("Installationsziel existiert bereits (kein Überschreiben)")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise WorkerBundleError("Übergeordneter Installationsordner fehlt")
    if not apply:
        return {**result, "installed": False}
    # Die ZIP wird nicht erneut geöffnet. Nur bereits verifizierte Bytes
    # werden kopiert. mkdir(exist_ok=False) reserviert das Ziel exklusiv,
    # auch wenn ein anderes Programm zeitgleich einen Ordner anlegt.
    target.mkdir(exist_ok=False)
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for name in FILES:
            dst = target / name
            if dst.parent not in created_dirs:
                dst.parent.mkdir(exist_ok=False)
                created_dirs.append(dst.parent)
            with dst.open("xb") as stream:
                created_files.append(dst)
                stream.write(payloads[name])
        return {**result, "installed": True}
    except BaseException:
        # Nur eigene Dateien/Ordner aufräumen, niemals fremden Inhalt
        # rekursiv löschen, falls parallel etwas hinzugefügt wurde.
        for file in reversed(created_files):
            file.unlink(missing_ok=True)
        for directory in [*reversed(created_dirs), target]:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
