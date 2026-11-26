"""Remove two unused Qt plugins and their GPL-sensitive dependencies.

Only operate on a generated Windows OneDir payload, after checking all PE
normal and delay imports. Never remove a DLL used by a retained binary.
"""
from __future__ import annotations
import argparse
from pathlib import Path

QT_FILES = (
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "plugins/qmltooling/qmldbg_quick3dprofiler.dll",
    "Qt6VirtualKeyboard.dll",
    "Qt6Quick3DUtils.dll",
)

def pe_imports(path: Path) -> set[str]:
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ])
        return {entry.dll.decode("ascii").lower()
                for key in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
                for entry in getattr(pe, key, [])}
    finally:
        pe.close()

def prune(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    if not (root / "OMOI-Mini.exe").is_file():
        raise ValueError("Not an OMOI Mini OneDir build")
    qt = root / "_internal" / "PySide6"
    if not qt.is_dir() or qt.is_symlink():
        raise ValueError("Qt payload missing")
    targets = {qt / rel for rel in QT_FILES if (qt / rel).exists()}
    if not targets:
        return []
    if any(p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(qt.resolve())
           for p in targets):
        raise ValueError("Unexpected path in Qt pruning set")
    forbidden = {p.name.lower() for p in targets}
    for path in root.rglob("*"):
        if path in targets or path.suffix.lower() not in {".dll", ".exe", ".pyd"}:
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError("Unexpected PE path")
        dependencies = pe_imports(path) & forbidden
        if dependencies:
            raise ValueError(f"Retained binary {path.relative_to(root)} needs {sorted(dependencies)}")
    removed = []
    for path in sorted(targets):
        path.unlink()
        removed.append(path.relative_to(root).as_posix())
    return removed

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print("QT_PRUNE_OK", prune(args.folder))
