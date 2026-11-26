"""Read-only public VRMA gate; kein Modell-/GPU-/Cloud-Zugriff."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from omoi_mini.public_assets import PublicAssetError, validate_public_animations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path,
                        default=ROOT / "assets" / "default-animations")
    args = parser.parse_args()
    try:
        clips = validate_public_animations(args.assets)
    except (PublicAssetError, OSError) as error:
        print(f"PUBLIC_ANIMATIONS_BLOCKED: {error}", file=sys.stderr)
        return 2
    print(f"PUBLIC_ANIMATIONS_OK count={len(clips)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
