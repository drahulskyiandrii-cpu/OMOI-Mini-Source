"""Modellinstallation: isolierter, revisionsgebundener Kern ohne Netzwerk/Inference."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


class InstallError(RuntimeError):
    pass


class InstallCancelled(InstallError):
    pass


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    model_id: str
    revision: str
    files: tuple[ModelFile, ...]


MARKER = ".omoi-managed.json"
RESERVE = 64 * 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}


def _segment(value: str) -> bool:
    return (bool(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value))
            and not value.endswith(".") and value.split(".")[0].upper() not in _RESERVED)


def validate(manifest: Manifest) -> int:
    if not _segment(manifest.model_id) or not re.fullmatch(r"[0-9a-f]{40}", manifest.revision):
        raise InstallError("Modell-ID oder Revision ungültig")
    if not manifest.files:
        raise InstallError("Leeres Manifest")
    names: set[str] = set()
    dirs: dict[str, str] = {}
    total = 0
    for file in manifest.files:
        parts = file.path.split("/")
        key = file.path.casefold()
        if (not all(_segment(part) for part in parts)
                or key in names or type(file.size) is not int or file.size <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", file.sha256)):
            raise InstallError("Ungültige Manifestdatei")
        names.add(key)
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if dirs.setdefault(parent.casefold(), parent) != parent:
                raise InstallError("Mehrdeutiger Verzeichnispfad")
        total += file.size
    if names & dirs.keys():
        raise InstallError("Datei-/Verzeichniskonflikt")
    return total


def _plain(path: Path, directory: bool) -> None:
    info = path.lstat()
    if (getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise InstallError("Kein regulärer Pfad (Link/Reparse-Point)")
    if not directory and info.st_nlink != 1:
        raise InstallError("Hardlinks sind nicht zulässig")


def _root(root: Path) -> Path:
    root = root.absolute()
    for part in reversed((root, *root.parents)):
        _plain(part, True)
    return root


def _metadata(manifest: Manifest) -> str:
    return json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))


def _check_cancel(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise InstallCancelled("Installation abgebrochen")


def install(root: Path, manifest: Manifest,
            source: Callable[[str, ModelFile], Iterable[bytes]], *,
            approved: bool = False,
            cancel: Callable[[], bool] = lambda: False,
            progress: Callable[[int, int], None] = lambda done, total: None) -> Path:
    """Nur explizit freigegebene Manifeste; Quelle erhält die exakte Revision.
    Keine Aktivierung, keine externen Prozesse. Marker erst nach Gesamtprüfung.
    """
    total = validate(manifest)
    if not approved:
        raise InstallError("Explizite Freigabe fehlt")
    root = _root(root)
    target = root / manifest.model_id
    _check_cancel(cancel)
    if shutil.disk_usage(root).free < total + RESERVE:
        raise InstallError("Nicht genügend freier Speicher")
    # Exklusiv: auch ein vorhandener leerer Ordner wird nicht übernommen.
    target.mkdir()
    created_files: list[Path] = []
    created_dirs = [target]
    done = 0
    try:
        progress(0, total)
        for file in manifest.files:
            _check_cancel(cancel)
            path = target / file.path
            for parent in reversed(path.relative_to(target).parents):
                if parent == Path("."):
                    continue
                directory = target / parent
                if directory not in created_dirs:
                    directory.mkdir()
                    created_dirs.append(directory)
                _plain(directory, True)
            digest = hashlib.sha256()
            size = 0
            with path.open("xb") as out:
                created_files.append(path)
                for chunk in source(manifest.revision, file):
                    _check_cancel(cancel)
                    if not isinstance(chunk, bytes) or size + len(chunk) > file.size:
                        raise InstallError("Unerwartete Dateigröße oder Daten")
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    done += len(chunk)
                    progress(done, total)
            if size != file.size or digest.hexdigest() != file.sha256:
                raise InstallError("SHA-256 oder Größe stimmt nicht")
        _check_cancel(cancel)
        marker = target / MARKER
        with marker.open("x", encoding="utf-8") as out:
            created_files.append(marker)
            out.write(_metadata(manifest))
        return target
    except BaseException:
        # Keine rekursive Löschung: nur selbst angelegte Dateien/leere Ordner.
        for path in reversed(created_files):
            try:
                _plain(path, False)
                path.unlink()
            except (OSError, InstallError):
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def remove(root: Path, manifest: Manifest, *, approved: bool = False) -> None:
    """Nur vollständig verifizierte, unveränderte verwaltete Installation löschen."""
    validate(manifest)
    if not approved:
        raise InstallError("Explizite Löschfreigabe fehlt")
    target = _root(root) / manifest.model_id
    _plain(target, True)
    marker = target / MARKER
    _plain(marker, False)
    if marker.stat().st_size > 1024 * 1024 or marker.read_text(encoding="utf-8") != _metadata(manifest):
        raise InstallError("Verwaltungsmanifest stimmt nicht")
    dirs: set[Path] = set()
    paths: list[Path] = []
    for file in manifest.files:
        path = target / file.path
        for parent in path.relative_to(target).parents:
            if parent != Path("."):
                dirs.add(target / parent)
        paths.append(path)
    for directory in sorted(dirs, key=lambda p: len(p.parts)):
        _plain(directory, True)
    for file, path in zip(manifest.files, paths):
        _plain(path, False)
        if path.stat().st_size != file.size:
            raise InstallError("Dateigröße geändert")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != file.sha256:
                raise InstallError("Datei verändert")
    expected = set(paths) | dirs | {marker}
    # Keine fremden Dateien oder Unterordner; Links werden nicht verfolgt.
    actual = {child for directory in (target, *dirs) for child in directory.iterdir()}
    if actual != expected:
        raise InstallError("Fremde Dateien im Modellordner")
    for path in paths:
        path.unlink()
    marker.unlink()
    for directory in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        directory.rmdir()
    target.rmdir()
