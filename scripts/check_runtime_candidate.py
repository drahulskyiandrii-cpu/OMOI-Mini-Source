"""Statische Windows-Python-3.11-Qualifikation, kein Interpreter-/Modellstart."""
from __future__ import annotations
import argparse
import json
from importlib import metadata
from pathlib import Path
import re
import sys
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import cpython_tags,compatible_tags,parse_tag
from packaging.utils import canonicalize_name
from packaging.version import Version

# Kandidaten ohne abgeschlossene Laufzeitpruefung.
REQUIRED={"filelock":"4.3.1","pillow":"13.0.0",
          "torch":"2.9.0+cu128","torchaudio":"2.9.0+cu128",
          "faster-qwen3-tts":"0.5.4","faster-whisper":"1.2.1",
          "ctranslate2":"4.9.0"}


def check_candidate(root:Path,overlay:Path|None=None):
    root=root.resolve()
    cfg=root/"pyvenv.cfg"
    if not cfg.is_file() or cfg.stat().st_size>16384:
        raise ValueError("Missing venv configuration")
    version=None
    isolated=False
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key,sep,value=line.partition("=")
        if sep and key.strip()=="version":
            version=value.strip()
        if sep and key.strip()=="include-system-site-packages":
            isolated=value.strip().lower()=="false"
    if not version or not re.fullmatch(r"3\.11\.\d+",version):
        raise ValueError("Python 3.11 required")
    if not isolated or not (root/"Scripts/python.exe").is_file():
        raise ValueError("Isolated candidate venv required")
    sites=[root/"Lib/site-packages"]
    if overlay is not None and overlay.resolve()!=sites[0].resolve():
        sites.append(overlay.resolve())
    if not all(p.is_dir() for p in sites):raise ValueError("Missing package directory")
    env=default_environment()
    env.update(python_version="3.11",python_full_version=version,
               sys_platform="win32",platform_system="Windows",
               platform_machine="AMD64",os_name="nt",extra="")
    supported=set(cpython_tags((3,11),platforms=["win_amd64"]))
    supported.update(compatible_tags((3,11),interpreter="cp311",platforms=["win_amd64"]))
    distributions={}
    duplicate=0
    bad_python=bad_wheel=direct_url=0
    for dist in metadata.distributions(path=[str(p) for p in sites]):
        name=canonicalize_name(dist.metadata["Name"] or "")
        if not name:raise ValueError("Invalid metadata")
        if name in distributions:
            duplicate+=1
            continue
        distributions[name]=dist
        requires=dist.metadata.get("Requires-Python") or ""
        if version not in SpecifierSet(requires):bad_python+=1
        wheel=dist.read_text("WHEEL") or ""
        tags=set()
        for line in wheel.splitlines():
            if line.startswith("Tag:"):tags.update(parse_tag(line[4:].strip()))
        if not tags&supported:bad_wheel+=1
        if dist.read_text("direct_url.json"):direct_url+=1
    missing=conflicts=0
    for dist in distributions.values():
        for raw in dist.requires or []:
            requirement=Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(env):continue
            dependency=distributions.get(canonicalize_name(requirement.name))
            if dependency is None:missing+=1
            elif Version(dependency.version) not in requirement.specifier:conflicts+=1
    required_mismatch=[name for name,version in REQUIRED.items()
                       if name not in distributions or Version(distributions[name].version)!=Version(version)]
    result={"python":version,"packages":len(distributions),"duplicate_packages":duplicate,
            "requires_python_conflicts":bad_python,"incompatible_or_missing_wheel_tags":bad_wheel,
            "missing_dependencies":missing,"version_conflicts":conflicts,
            "required_mismatch":required_mismatch,"direct_url_review":direct_url}
    result["static_pass"]=not any((duplicate,bad_python,bad_wheel,missing,conflicts,required_mismatch))
    # Auch bei PASS: Herkunft, Hashes, native DLLs, Extras und Live-Abnahme offen.
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate",type=Path)
    parser.add_argument("--overlay",type=Path)
    args=parser.parse_args()
    try:result=check_candidate(args.candidate,args.overlay)
    except (OSError,ValueError,TypeError,KeyError):
        print("RUNTIME_CANDIDATE_INVALID",file=sys.stderr)
        return 2
    print(json.dumps(result,indent=2))
    return 0 if result["static_pass"] else 1


if __name__=="__main__":raise SystemExit(main())
