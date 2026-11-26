"""Statische Windows-OneDir-Prüfung ohne Ausführen der EXE oder Modelle.

Diese Inventur prüft Dateinamen, Strukturen und das freigegebene
Standardanimationsverzeichnis. Kein dynamischer Security-/Lizenzbeweis.
"""
from __future__ import annotations

from pathlib import Path

from .public_assets import PublicAssetError, validate_public_animations
from .default_avatar import approved_default_avatar

BLOCKED_SUFFIXES = frozenset({
    ".gguf", ".safetensors", ".onnx", ".pt", ".pth",
    ".wav", ".mp3", ".flac", ".ogg", ".opus", ".pcm",
    ".vrm", ".sqlite", ".sqlite3", ".db", ".token", ".key",
})
BLOCKED_PARTS = frozenset({
    "user-data", "recordings", "personal-voices", "tts-eval",
    "stt-eval", "model_cache", "weights", "checkpoints",
    "aicompanion", "credentials", "secrets",
})


class PortableAuditError(ValueError):
    """Portable-Paket verletzt eine technische Datenfreigabe-Grenze."""


def inspect_portable_folder(folder: Path) -> dict[str, int | bool]:
    """Erlaubt VRMA nur mit verifiziertem Komplettmanifest im Bundle."""
    root = Path(folder)
    if not root.is_dir() or root.is_symlink():
        raise PortableAuditError("Windows-Paketordner fehlt oder ist verlinkt")
    exe = root / "OMOI-Mini.exe"
    html = root / "_internal" / "web" / "dist" / "index.html"
    if not exe.is_file() or exe.is_symlink() or exe.stat().st_size < 2:
        raise PortableAuditError("Windows-EXE fehlt oder ist beschädigt")
    with exe.open("rb") as executable:
        if executable.read(2) != b"MZ":
            raise PortableAuditError("Windows-EXE ist kein PE-Programm")
    if not html.is_file() or html.is_symlink():
        raise PortableAuditError("Verpackte Weboberfläche fehlt")

    approved = root / "_internal" / "assets" / "default-animations"
    try:
        animations = validate_public_animations(approved)
    except (OSError, PublicAssetError):
        raise PortableAuditError("Mitgelieferte VRMA sind nicht vollständig freigegeben") from None
    allowed_animations = {p.resolve() for p in animations}
    try:
        avatar = approved_default_avatar(root / '_internal' / 'assets' / 'default-avatar')
    except (OSError, ValueError):
        raise PortableAuditError('Default avatar approval failed') from None
    files = 0
    total = 0
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise PortableAuditError("Symbolischer Link in Portable-Paket verboten")
        if not entry.is_file():
            continue
        files += 1
        relative = entry.relative_to(root)
        parts = {component.lower() for component in relative.parts}
        if parts & BLOCKED_PARTS:
            raise PortableAuditError("Privates Benutzer-/Modellverzeichnis im Paket")
        suffix = entry.suffix.lower()
        if ((suffix in BLOCKED_SUFFIXES and not (suffix == '.vrm' and entry.resolve() == avatar))
                or entry.name.lower() in {".env", "profiles.json", "omoi-mini.sqlite3"}
                or entry.name.lower().startswith(".env.")):
            raise PortableAuditError("Persönliche Datei oder Modellgewicht im Paket")
        if suffix == ".vrma" and entry.resolve() not in allowed_animations:
            raise PortableAuditError("Nicht freigegebene VRMA im Paket")
        total += entry.stat().st_size
    if files < 2:
        raise PortableAuditError("Unvollständiger Paketinhalt")
    return {"file_count": files, "total_bytes": total,
            "animation_count": len(animations), "static_audit_passed": True}
