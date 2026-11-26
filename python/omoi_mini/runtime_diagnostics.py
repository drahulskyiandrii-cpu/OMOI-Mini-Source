"""Reine Dateiprüfung für eine unabhängige Mini-Worker-Installation.

Weder Prozessstart noch Netzwerk, Modellimport, GPU-Abfrage oder Downloads.
Das Ergebnis enthält nur Booleans, nie private Pfade oder Modellnamen.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping

from .core import external_runtime_root
from .runtime_config import runtime_paths


def inspect_local_runtimes(
    root: Path | None = None,
    user_base: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    node_found: bool | None = None,
) -> dict[str, dict[str, bool]]:
    """Prüft ausschließlich sichtbare Dateien, nicht die Lauffähigkeit der KI."""
    env = os.environ if env is None else env
    if root is None:
        configured_root = env.get("OMOI_MINI_RUNTIME_ROOT", "").strip()
        root = Path(configured_root).expanduser().resolve() if configured_root else external_runtime_root()
    else:
        root = Path(root)
    user_base = (Path(env.get("LOCALAPPDATA") or Path.home()) / "OMOI-Mini"
                 if user_base is None else Path(user_base))
    stt = root / "local-stt"
    tts = root / "local-tts"
    node = bool(shutil.which("node")) if node_found is None else node_found
    configured_openvino = env.get("OMOI_MINI_STT_MODEL", "").strip()
    openvino_path = Path(configured_openvino).expanduser() if configured_openvino else None
    paths = runtime_paths(env, user_base)
    cuda_python = paths.cuda_python
    qwen_python = paths.qwen_python
    overlay = paths.cuda_overlay
    cuda_model = paths.cuda_model / "model.bin"
    piper_python = paths.piper_python

    return {
        "openvino_stt": {
            "worker_file": (stt / "worker.mjs").is_file(),
            "node_available": bool(node),
            "node_dependency": (stt / "node_modules" / "openvino-genai-node").is_dir(),
            "model_explicit": bool(configured_openvino),
            "model_directory": bool(openvino_path and openvino_path.is_dir()),
        },
        "cuda_stt": {
            "worker_file": (stt / "cuda_worker.py").is_file(),
            "python_runtime": cuda_python.is_file(),
            "python_overlay": overlay.is_dir(),
            "model_file": cuda_model.is_file(),
        },
        "qwen_tts": {
            "server_file": (tts / "qwen_server.py").is_file(),
            "python_runtime": qwen_python.is_file(),
            # Gewichtscache/CUDA/Referenzstimme hier absichtlich nicht lesen.
        },
        "piper_tts": {
            "worker_file": (tts / "piper_worker.py").is_file(),
            "python_runtime": piper_python.is_file(),
        },
    }
