"""Gemeinsame Pfade der lokalen Adapter und statischen Diagnose.

Keine Verzeichniserstellung, kein Import von KI-Bibliotheken, kein Prozessstart.
Explizite Umgebungsvariablen ermöglichen unabhängige Worker-Installationen.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# SQLite-Schlüssel -> etablierte Umgebungsvariable; keine beliebigen Env-Overrides.
RUNTIME_SETTINGS = {
    "runtime_root": "OMOI_MINI_RUNTIME_ROOT",
    "runtime_openvino_model": "OMOI_MINI_STT_MODEL",
    "runtime_cuda_python": "OMOI_MINI_CUDA_STT_PYTHON",
    "runtime_cuda_overlay": "OMOI_MINI_CUDA_STT_OVERLAY",
    "runtime_cuda_model": "OMOI_MINI_CUDA_STT_MODEL",
    "runtime_qwen_python": "OMOI_MINI_QWEN_PYTHON",
    "runtime_piper_python": "OMOI_MINI_PIPER_PYTHON",
}
PYTHON_SETTINGS = frozenset({
    "runtime_cuda_python", "runtime_qwen_python", "runtime_piper_python",
})


def validate_runtime_setting(key: str, value: object) -> None:
    if key not in RUNTIME_SETTINGS or not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Ungültiger Laufzeitpfad")
    if value and ("\x00" in value or not Path(value).is_absolute()):
        raise ValueError("Absoluter Laufzeitpfad erforderlich")


def runtime_overrides(settings: Mapping[str, object]) -> dict[str, str]:
    result = {}
    for key, env_key in RUNTIME_SETTINGS.items():
        value = settings.get(key, "")
        validate_runtime_setting(key, value)
        if value:
            result[env_key] = value
    return result


def runtime_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    # Nur Kindprozesse konfigurieren, nie os.environ verändern.
    # Lokale KI benoetigt keine Cloud-Schluessel. Nur Variablennamen
    # pruefen; keine Schluesselwerte protokollieren oder weitergeben.
    secret_suffixes=("_API_KEY","_TOKEN","_PASSWORD","_SECRET","_ACCESS_KEY")
    secret_names={"AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","HF_TOKEN",
                  "HUGGING_FACE_HUB_TOKEN","GITHUB_TOKEN","GH_TOKEN","CARTESIA_VOICE_ID"}
    result={key:value for key,value in os.environ.items()
            if key.upper() not in secret_names and not key.upper().endswith(secret_suffixes)}
    if overrides:
        result.update({key: value for key, value in overrides.items()
                       if key in RUNTIME_SETTINGS.values() and value})
    return result


def runtime_path_sources(settings: Mapping[str, object]) -> dict[str, str]:
    return {key: "mini" if settings.get(key) else
            "environment" if os.environ.get(env_key, "").strip() else "default"
            for key, env_key in RUNTIME_SETTINGS.items()}


def configured_worker_root(overrides: Mapping[str, str] | None = None) -> Path | None:
    value = (overrides or {}).get("OMOI_MINI_RUNTIME_ROOT", "")
    return Path(value).resolve() if value else None


@dataclass(frozen=True)
class RuntimePaths:
    cuda_python: Path
    cuda_overlay: Path
    cuda_model: Path
    qwen_python: Path
    piper_python: Path


def runtime_paths(
    env: Mapping[str, str] | None = None,
    user_base: Path | None = None,
) -> RuntimePaths:
    env = os.environ if env is None else env
    base = (Path(env.get("LOCALAPPDATA") or Path.home()) / "OMOI-Mini"
            if user_base is None else Path(user_base))
    legacy_qwen = base / "tts-eval" / "qwen-venv" / "Scripts" / "python.exe"

    def selected(key: str, default: Path) -> Path:
        value = env.get(key, "").strip()
        # Absolute Pfade auch im Worker mit abweichendem cwd konsistent halten.
        return Path(value or default).expanduser().resolve()

    return RuntimePaths(
        cuda_python=selected("OMOI_MINI_CUDA_STT_PYTHON", legacy_qwen),
        cuda_overlay=selected("OMOI_MINI_CUDA_STT_OVERLAY",
                              base / "stt-eval" / "faster-whisper-overlay"),
        cuda_model=selected("OMOI_MINI_CUDA_STT_MODEL",
                            base / "stt-eval" / "faster-whisper-small-ct2"),
        qwen_python=selected("OMOI_MINI_QWEN_PYTHON", legacy_qwen),
        piper_python=selected("OMOI_MINI_PIPER_PYTHON",
                              base / "tts-eval" / "piper-venv" / "Scripts" / "python.exe"),
    )
