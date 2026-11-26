"""Unabhängiges Worker-Quellpaket: bauen, prüfen, trocken installieren.

Absichtlich keine Subprocess-, Paketmanager-, Cloud- oder GPU-Aufrufe.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from omoi_mini.worker_bundle import (
    WorkerBundleError, build_worker_bundle, verify_worker_bundle,
    install_worker_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="Nur Worker-Quellcode (keine Modelle)")
    build.add_argument("--source", type=Path, default=ROOT)
    build.add_argument("--output", type=Path, required=True)
    verify = actions.add_parser("verify", help="Strenge ZIP- und SHA256-Prüfung")
    verify.add_argument("bundle", type=Path)
    install = actions.add_parser("install", help="Ohne --apply nur Dry-run")
    install.add_argument("bundle", type=Path)
    install.add_argument("--target", required=True, type=Path)
    install.add_argument("--apply", action="store_true",
                         help="Explizit in NEUES, noch nicht existierendes Verzeichnis kopieren")
    args = parser.parse_args()
    try:
        if args.action == "build":
            result = build_worker_bundle(args.source, args.output)
        elif args.action == "verify":
            result = verify_worker_bundle(args.bundle)
        else:
            result = install_worker_bundle(
                args.bundle, args.target, apply=args.apply)
    except (WorkerBundleError, OSError) as error:
        print("WORKER_BUNDLE_REJECTED: " + str(error), file=sys.stderr)
        return 2
    print("WORKER_BUNDLE_OK " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
