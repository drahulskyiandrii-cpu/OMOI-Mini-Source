"""Read-only Mini-Worker-Diagnose; startet oder installiert nichts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from omoi_mini.runtime_diagnostics import inspect_local_runtimes


if __name__ == "__main__":
    # Keine Profilpfade, Schlüsselnamen, persönlichen Dateien oder IDs ausgeben.
    report = inspect_local_runtimes()
    print(json.dumps({
        "status": "STATIC_FILES_ONLY",
        "workers": report,
        "warning": "Keine Aussage über CUDA, Gewichte, Lizenzen oder echte Inferenz."
    }, indent=2, sort_keys=True))
