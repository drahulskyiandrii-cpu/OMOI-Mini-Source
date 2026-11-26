"""Serialisierter Modellservice: feste Profilwurzel, Allowlist, HTTPS.

Keine automatische Aktivierung oder Inferenz. Alle echten Releases bleiben
bis zur expliziten Rechte- und Runtime-Freigabe gesperrt.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import (build_opener, HTTPRedirectHandler, ProxyHandler, Request)
from urllib.error import URLError

from . import model_installer as core
from .model_releases import ModelRelease, RELEASES

ROOT_NAME = "managed-models"
LOCK = ".operation.lock"
CHUNK = 256 * 1024


def _safe_https(url: str) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.fragment or not (
                host == "huggingface.co" or host.endswith(".huggingface.co")
                or host.endswith(".hf.co"))):
        raise core.InstallError("Nicht erlaubtes Downloadziel")


class _Redirect(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_source(release: ModelRelease):
    """Nur manifestgebundene Dateien; keine Tokens, kein automatischer Retry."""
    if not release.approved:
        raise core.InstallError("Modellfreigabe fehlt")
    core.validate(release.manifest)
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*",
                        release.repository):
        raise core.InstallError("Repository ungültig")
    opener = build_opener(ProxyHandler({}), _Redirect())

    def source(revision, file):
        if revision != release.manifest.revision or file not in release.manifest.files:
            raise core.InstallError("Datei nicht im freigegebenen Manifest")
        url = f"https://huggingface.co/{release.repository}/resolve/{revision}/{file.path}"
        _safe_https(url)
        try:
            request = Request(url, headers={"Accept-Encoding": "identity"})
            with opener.open(request, timeout=15) as response:
                _safe_https(response.geturl())
                if response.status != 200:
                    raise core.InstallError("Unerwarteter HTTP-Status")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise core.InstallError("Unerwartete HTTP-Kodierung")
                length = response.headers.get("Content-Length")
                if length is not None and length != str(file.size):
                    raise core.InstallError("Unerwartete HTTP-Dateigröße")
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk
        except (URLError, OSError, ValueError) as exc:
            # Keine signierten URLs, Header oder privaten Details weitergeben.
            raise core.InstallError("Modelltransfer fehlgeschlagen") from None
    return source


class ModelService:
    """Profilpfad stammt ausschließlich vom Python-Host, niemals vom WebClient."""
    def __init__(self, profile: Path, *, releases=RELEASES, source_factory=https_source):
        self._profile = Path(profile).absolute()
        self._releases = tuple(releases)
        self._source_factory = source_factory

    def _release(self, model_id: str) -> ModelRelease:
        release = next((r for r in self._releases if r.manifest.model_id == model_id), None)
        if release is None or not release.approved:
            raise core.InstallError("Modell nicht zur Installation freigegeben")
        core.validate(release.manifest)
        return release

    @contextmanager
    def _operation(self):
        profile = core._root(self._profile)
        root = profile / ROOT_NAME
        root.mkdir(exist_ok=True)
        core._root(root)
        lock = root / LOCK
        try:
            with lock.open("x", encoding="ascii") as stream:
                stream.write("OMOI Mini model operation\n")
        except FileExistsError:
            raise core.InstallError("Modelloperation läuft oder benötigt manuelle Prüfung") from None
        try:
            yield root
        finally:
            core._plain(lock, False)
            lock.unlink()

    def install(self, model_id: str, *, approved=False, cancel=lambda: False,
                progress=lambda done, total: None) -> Path:
        release = self._release(model_id)
        if not approved:
            raise core.InstallError("Explizite Freigabe fehlt")
        with self._operation() as root:
            return core.install(root, release.manifest, self._source_factory(release),
                                approved=True, cancel=cancel, progress=progress)

    def remove(self, model_id: str, *, approved=False) -> None:
        # Nicht aus der GUI aufrufen, solange aktive Modellpfade nicht geprüft sind.
        release = self._release(model_id)
        if not approved:
            raise core.InstallError("Explizite Löschfreigabe fehlt")
        with self._operation() as root:
            core.remove(root, release.manifest, approved=True)
