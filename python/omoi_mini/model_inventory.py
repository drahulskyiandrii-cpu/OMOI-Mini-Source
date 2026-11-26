"""Read-only Installationsplanung: keine Hash-Lektuere grosser Gewichte."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Mapping

from . import model_installer as core
from .model_catalog import catalog_rows
from .model_releases import release_for
from .model_service import ROOT_NAME, LOCK
from .runtime_config import runtime_environment, runtime_overrides, runtime_paths


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _candidate_file(path: Path, *, gguf: bool = False) -> bool:
    try:
        if not path.is_absolute() or not path.is_file() or path.stat().st_size <= 0:
            return False
        if gguf:
            with path.open("rb") as stream:
                return stream.read(4) == b"GGUF"
        return True
    except (OSError, ValueError):
        return False


def _matching_sizes(root: Path, manifest, *, strict: bool) -> bool:
    try:
        if strict:
            core._root(root)
        elif not root.is_dir():
            return False
        for file in manifest.files:
            path = root / file.path
            # README is provenance, not required by the existing runtime.
            if not strict and file.path == "README.md":
                continue
            if strict:
                core._root(path.parent)
                core._plain(path, False)
            elif not path.is_file():
                return False
            if path.stat().st_size != file.size:
                return False
        return True
    except (OSError, ValueError, core.InstallError):
        return False


def _managed_state(root: Path, manifest) -> str:
    if not _exists(root):
        return "absent"
    core._root(root)
    if _exists(root / LOCK):
        return "busy"
    target = root / manifest.model_id
    if not _exists(target):
        return "absent"
    try:
        core._root(target)
        marker = target / core.MARKER
        core._plain(marker, False)
        if (marker.stat().st_size <= 1024 * 1024
                and marker.read_text(encoding="utf-8") == core._metadata(manifest)
                and _matching_sizes(target, manifest, strict=True)):
            return "manifest_sizes_match"
    except (OSError, ValueError, core.InstallError):
        pass
    return "conflict"


def _external_state(model_id: str, settings: Mapping, env: Mapping[str, str], profile: Path) -> str:
    release = release_for(model_id)
    if model_id in {"gemma4-e2b-gguf", "gemma4-12b-gguf"}:
        key = "llm_model_file" if model_id == "gemma4-e2b-gguf" else "llm_gemma12_file"
        raw = settings.get(key)
        if not isinstance(raw, str) or not raw:
            return "not_configured"
        path = Path(raw)
        if not _candidate_file(path, gguf=True):
            return "configured_missing_or_invalid"
        if release and any(f.path.endswith(".gguf") and f.size == path.stat().st_size
                           for f in release.manifest.files):
            return "candidate_sizes_match"
        return "configured_present_unverified"
    if model_id == "whisper-small-cuda":
        root = runtime_paths(env).cuda_model
    elif model_id == "whisper-medium-ov":
        raw = env.get("OMOI_MINI_STT_MODEL", "")
        if not raw:
            return "not_configured"
        root = Path(raw)
    elif model_id == "qwen3-tts-cf050":
        raw = env.get("OMOI_MINI_QWEN_CF50_ADAPTER")
        adapter = Path(raw) if raw else profile / "tts-eval" / "cf-lora" / "adapter.safetensors"
        if not adapter.is_absolute() or not adapter.is_file():
            return "not_found"
        if adapter.stat().st_size == 34908456 and (adapter.parent / "adapter.json").is_file():
            return "configured_present_unverified"
        return "candidate_incomplete_or_different"
    elif model_id in {"qwen3-tts-1.7b", "qwen3-tts-0.6b"} and release:
        cache = Path(env.get("HF_HUB_CACHE") or env.get("HUGGINGFACE_HUB_CACHE") or
                     str(Path(env.get("HF_HOME") or Path.home()/".cache"/"huggingface")/"hub"))
        root = cache / ("models--" + release.repository.replace("/", "--")) / "snapshots" / release.manifest.revision
    else:
        return "not_checked"
    if not root.is_absolute() or not root.is_dir():
        return "not_found"
    return ("candidate_sizes_match" if release and _matching_sizes(root, release.manifest, strict=False)
            else "candidate_incomplete_or_different")


def installation_rows(profile: Path, settings: Mapping, *, env: Mapping[str, str] | None = None) -> list[dict]:
    """Nur Status, keine Pfade/Dateinamen nach aussen; keinerlei Schreiboperation."""
    profile = Path(profile).absolute()
    root = profile / ROOT_NAME
    effective_env = (runtime_environment(runtime_overrides(settings)) if env is None
                     else dict(env) | runtime_overrides(settings))
    free = None
    root_safe = False
    try:
        core._root(profile)
        if _exists(root):
            core._root(root)
        free = shutil.disk_usage(root if _exists(root) else profile).free
        root_safe = True
    except (OSError, ValueError, core.InstallError):
        pass
    rows = catalog_rows()
    for row in rows:
        release = release_for(row["id"])
        required = row.get("required_space_bytes")
        row["available_space_bytes"] = free
        row["enough_space"] = None if free is None or required is None else free >= required
        try:
            row["managed_status"] = (_managed_state(root, release.manifest) if root_safe and release
                                     else "unavailable" if release else "not_applicable")
        except (OSError, ValueError, core.InstallError):
            row["managed_status"] = "unavailable"
        try:
            row["existing_status"] = _external_state(row["id"], settings, effective_env, profile)
        except (OSError, ValueError, RuntimeError):
            row["existing_status"] = "unavailable"
    return rows
