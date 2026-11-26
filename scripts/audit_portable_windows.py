"""Read-only-Datei- und Namensaudit einer PyInstaller-OneDir-Ausgabe."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from omoi_mini.portable_audit import PortableAuditError, inspect_portable_folder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path,
                        default=ROOT / "dist" / "windows" / "OMOI-Mini")
    args = parser.parse_args()
    try:
        summary = inspect_portable_folder(args.folder)
    except (OSError, PortableAuditError) as exc:
        print("PORTABLE_STATIC_AUDIT_BLOCKED: " + str(exc), file=sys.stderr)
        return 2
    print("PORTABLE_STATIC_AUDIT_OK " + json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
