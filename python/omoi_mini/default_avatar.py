"""Single, hash-pinned third-party default avatar; never overwrite user choice."""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path
from .asset_library import validate_vrm, InvalidAvatar

DEFAULT_AVATAR_SHA256 = "1e177c1a7b14f783a9c48395831db8616260d3bddd4154cb2784b779adca49b5"

def approved_default_avatar(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Invalid default avatar folder")
    names = {p.name for p in folder.iterdir()}
    if not names <= {"default.vrm", "README.md"}:
        raise ValueError("Unexpected default avatar files")
    avatar = folder / "default.vrm"
    if not avatar.exists():
        return None
    notice = folder / "README.md"
    if not notice.is_file() or notice.is_symlink():
        raise ValueError("Default avatar notice missing")
    validate_vrm(avatar)
    if hashlib.sha256(avatar.read_bytes()).hexdigest() != DEFAULT_AVATAR_SHA256:
        raise ValueError("Default avatar hash mismatch")
    return avatar.resolve()

def bundled_default_avatar() -> Path | None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    try:
        return approved_default_avatar(root / "assets" / "default-avatar")
    except (OSError, InvalidAvatar, ValueError):
        return None
