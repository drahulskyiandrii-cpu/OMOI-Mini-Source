"""Lesbarer lokaler VRM-Katalog: keine Modelle, Metadaten oder Bilder in Git.

Die UI erhält nur einen begrenzten Index und Anzeigenamen; reale Pfade werden
ausschließlich auf der Python-Seite aufgelöst.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .asset_library import installed_mini_avatars
from .default_avatar import bundled_default_avatar

_MAX_VRM_SIZE = 200 * 1024 * 1024
_MAX_META_JSON = 8 * 1024 * 1024
_MAX_MODELS = 100


@dataclass(frozen=True)
class InstalledAvatar:
    path: Path
    label: str


def _metadata_name(path: Path) -> str:
    try:
        with path.open("rb") as file:
            header = file.read(20)
            if len(header) != 20:
                return ""
            magic, version, total = struct.unpack("<4sII", header[:12])
            json_size, kind = struct.unpack("<I4s", header[12:])
            if magic != b"glTF" or version != 2 or total != path.stat().st_size:
                return ""
            if kind != b"JSON" or not (2 <= json_size <= _MAX_META_JSON):
                return ""
            document = json.loads(file.read(json_size))
        if not isinstance(document, dict):
            return ""
        extensions = document.get("extensions") or {}
        if not isinstance(extensions, dict):
            return ""
        vrm1 = extensions.get("VRMC_vrm") or {}
        vrm0 = extensions.get("VRM") or {}
        metadata = (vrm1.get("meta", {}) if isinstance(vrm1, dict) else {}) or (
            vrm0.get("meta", {}) if isinstance(vrm0, dict) else {})
        if not isinstance(metadata, dict):
            return ""
        raw = metadata.get("name") or metadata.get("title") or ""
        if not isinstance(raw, str):
            return ""
        label = re.sub(r"\s+", " ", raw).strip()
        return label[:65] if label not in {"0", "1", "00", "stale", "GENERAL"} else ""
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return ""


def installed_avatars() -> list[InstalledAvatar]:
    result: list[InstalledAvatar] = []
    default = bundled_default_avatar()
    if default is not None:
        result.append(InstalledAvatar(default, 'Standardavatar (VRoid Hub)'))
    for path in installed_mini_avatars()[:_MAX_MODELS]:
        try:
            if path.is_symlink() or not path.is_file() or not 100 <= path.stat().st_size < _MAX_VRM_SIZE:
                continue
            friendly = _metadata_name(path) or f"VRM-Modell {len(result) + 1:02d}"
            result.append(InstalledAvatar(path.resolve(), f"{friendly} · #{len(result) + 1:02d}"))
        except OSError:
            continue
    return result
