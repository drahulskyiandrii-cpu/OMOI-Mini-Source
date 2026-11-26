"""Kompaktes, reproduzierbares Quellpaket aus einem festen Git-Stand."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PREFIX="OMOI-Mini"
AVATAR="assets/default-avatar/default.vrm"
AVATAR_SHA="1e177c1a7b14f783a9c48395831db8616260d3bddd4154cb2784b779adca49b5"
APPROVED_ANIMATIONS = {
    "assets/default-animations/annoyed.vrma":
        "1e8fb16b889c9054cc44023df63932ff119a87ddab8c5e0f05378a66318b5869",
    "assets/default-animations/excited.vrma":
        "8f64bb3a91a7a98127eef3820e2e284cfc2d2eb54dd336823f83fd4ef3d07412",
    "assets/default-animations/explaining.vrma":
        "911f108951fba12ea151649b7749e2cee00ab7f9384ddde8a750c441886d0b91",
    "assets/default-animations/happy.vrma":
        "fbe186a11ce3cdb2e0dec7cdf53930f6571c68a574a5adf073f9cf021284fcf8",
    "assets/default-animations/happy_alt1.vrma":
        "7bc4b0a91cd1feb0afee4600d5f8d800ba3bc64c07b96ca0faf637ed6da6f9e1",
    "assets/default-animations/happy_alt2.vrma":
        "55b69cf6ed4213adeba1279cb85015c136cbaa5ecefc5211f4f595fc454d6b39",
    "assets/default-animations/idle.vrma":
        "9563c582418bd5b51da473ea85b41722cb5304f84128935c3b570c72316c444a",
    "assets/default-animations/idle_alt1.vrma":
        "7985c8621c2fb965b7d6f5236efa885cfa7fa92995d544f470dc0028864efa13",
    "assets/default-animations/idle_alt2.vrma":
        "53fef1f07988adceb758ec2a9dc92ee29410698da8e1a0a49d156a662756800e",
    "assets/default-animations/idle_shift.vrma":
        "e1427084fb837451d2f096845177c0af1614809bceb873c1b113b748f094b1b1",
    "assets/default-animations/idle_shift_alt1.vrma":
        "5e3849bc677f63fb5952583c39102ffdf1adb08b32baa3e4b9de54630a3bb3b9",
    "assets/default-animations/idle_shift_alt2.vrma":
        "dcee96eeb16fd4a204488a7f5c20871ac4ddc5c82aec3bd47d462bda32668b0f",
    "assets/default-animations/listening.vrma":
        "5d25dcdaf841c879d056f299b1dc28515da11ddf95e3d26ea24ef5419281a2da",
    "assets/default-animations/listening_alt1.vrma":
        "a4185076435c7797d169a8e2bd8206696805c46d91d8194ecbe589cd6be24d34",
    "assets/default-animations/nod.vrma":
        "c5f715092eacacc827b52c984ebd5c8b8d991b2e34102fbc03235b01c6bcbbe8",
    "assets/default-animations/nod_alt1.vrma":
        "6a0b24b783b8436f61dc66d87aaecff96b8b9226b58a20d051c4a660129a8d84",
    "assets/default-animations/nod_alt2.vrma":
        "6e00b091e2c26c7f860f194d9931c2538ce73a5d73579aaebfdf4a06ace49bb3",
    "assets/default-animations/sad.vrma":
        "7d011478fbff385bdde6ea0314a4035740571c5fdfb9902c8799fdb8ce189626",
    "assets/default-animations/shake_head.vrma":
        "99b6349e0df9eec08c50eb2285935114692bb0d9b0e78dd9f754a6f7e5fdce23",
    "assets/default-animations/shy.vrma":
        "91642c3593383d6b8e3d7ae2e5e408abac89a2baa31d2c1a3b625a46b0ab92b6",
    "assets/default-animations/talk_enthusiastic.vrma":
        "b5d9605ebb566fb811b52e81fa826ad3b47601d349994540c65a33a001ed1aac",
    "assets/default-animations/talk_gentle.vrma":
        "1648d8a03d7e563cad203fa88b80cfd7629e70292cbcc406bc730ac1ffc8bf60",
    "assets/default-animations/talking.vrma":
        "74f50c1b63bb8c2b19276dd7db26c9e8096da72acb521d4e635b059730e78bd8",
    "assets/default-animations/talking_alt1.vrma":
        "36be8475c4eba974ea6da2268903cd9461536e24a7cbb98f457acc62a4098f12",
    "assets/default-animations/thinking.vrma":
        "e28485c2606b4b27869dee84d0a4be7cff9d6eb1a573cf1ea33b3d5a4b5aa3f9",
    "assets/default-animations/thinking_alt1.vrma":
        "2b7d7e4cd922d9a9cdd8347b4921e57fbcccd423c315765e97c8866505c657f2",
    "assets/default-animations/welcome.vrma":
        "f63bf415512e740375c47306c4c0b9d921fcab64f45c80be994fe7d17dc9f4a0",
}
EXACT=frozenset({
    "README.md","AGENTS.md",".gitignore",".env.example","start.bat",
    "requirements.txt","requirements-lock.txt","requirements-build.txt","requirements-runtime-lock.txt","scripts/install_runtime.ps1","START-INSTALLED.bat",
    "docs/INSTALLATION.md","docs/DISTRIBUTION.md","docs/SCHOOL_ARCHITECTURE.md","docs/LICENSES.md","docs/INSTALLER.md","LICENSE","installer/OMOI-Mini.iss","installer/OMOI-Mini-Source.iss","scripts/build_source_installer.ps1",
    "assets/default-animations/README.md","assets/default-animations/LICENSES.example.json",
    "assets/default-animations/LICENSES.json","assets/default-animations/NOTICE.md","assets/default-animations/LICENSE-Apache-2.0.txt","assets/default-avatar/README.md",AVATAR,
    "web/default_animations.test.mjs",
    "local-stt/worker.mjs","local-stt/cuda_worker.py","local-stt/acoustic_worker.mjs",
    "local-stt/acoustic_worker.test.mjs","local-stt/package.json","local-stt/package-lock.json",
    "local-tts/qwen_server.py","local-tts/piper_worker.py",
    "web/index.html","web/package.json","web/package-lock.json",
    "web/animation_policy.test.mjs","web/animation_variants.test.mjs","web/face_motion.test.mjs","web/motion_blend.test.mjs","web/emotion_motion.test.mjs",
    "scripts/build_windows.ps1","scripts/prune_windows_qt.py","scripts/pyinstaller-hooks/hook-PySide6.QtQml.py","scripts/omoi_mini_entry.py",
    "scripts/validate_public_animations.py","scripts/audit_portable_windows.py",
    "scripts/inspect_local_runtimes.py","scripts/worker_bundle.py","scripts/stage_workers.py",
    "scripts/check_runtime_candidate.py","scripts/evaluate_stt_text.py",
    "scripts/evaluate_acoustic_evidence.py","scripts/benchmark_acoustic_cpu.py",
    "scripts/qualify_acoustic_cpu.py","scripts/export_source.py",
})
# Optional reviewed demonstration, also compatible with earlier source archives.
DEMO_DOC="docs/DEMO.md"
DEMO_IMAGE="docs/screenshots/standard-avatar.png"
DEMO_SHA="7a23fa43c14532a4b0e3640a4b72d4ec1dd296c10de2b88afd9c7d374ea2c98c"
PATTERNS=(
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{25,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN "+r"[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:[A-Za-z]:[/\\\\]+Users[/\\\\]+Student|[A-Za-z]:[/\\\\]+Projects[/\\\\]+AICompanion)",re.I),
)


def selected(name):
    path=PurePosixPath(name)
    return (name in EXACT or name in APPROVED_ANIMATIONS or name in (DEMO_DOC,DEMO_IMAGE)
            or (path.parent.as_posix()=="python/omoi_mini" and path.suffix==".py")
            or (path.parent.as_posix()=="python/tests" and path.name.startswith("test_") and path.suffix==".py")
            or (path.parent.as_posix()=="web/src" and path.suffix in (".js",".css")))


def scan(name,data):
    # Only the pinned redistributable avatar and reviewed motion bytes bypass text scan.
    if name == DEMO_IMAGE:
        if hashlib.sha256(data).hexdigest()!=DEMO_SHA:
            raise ValueError("Unapproved demonstration image")
        return data
    if name == AVATAR:
        if hashlib.sha256(data).hexdigest()!=AVATAR_SHA:
            raise ValueError("Unapproved default avatar")
        return data
    if name in APPROVED_ANIMATIONS:
        if hashlib.sha256(data).hexdigest()!=APPROVED_ANIMATIONS[name]:
            raise ValueError("Unapproved motion bytes: "+name)
        return data
    if not 0<len(data)<=2*1024*1024:raise ValueError("Unzulaessige Dateigroesse: "+name)
    text=data.decode("utf-8-sig")
    if "\x00" in text or any(p.search(text) for p in PATTERNS):
        raise ValueError("Private oder binaere Daten vermutet: "+name)
    if name==".env.example":
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                if not re.fullmatch(r"[A-Z_0-9]+=",line.strip()):
                    raise ValueError("Nicht leere Beispiel-Konfiguration")
    return data


def make_export(root,destination):
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    listing=subprocess.check_output(["git","ls-tree","-r","-z",commit],cwd=root)
    paths=[]
    for item in listing.split(b"\x00"):
        if not item:continue
        mode,_,name=item.partition(b"\t")
        name=name.decode("utf-8")
        if selected(name):
            if not mode.startswith((b"100644 blob ",b"100755 blob ")):
                raise ValueError("Nicht regulaere Git-Datei: "+name)
            paths.append(name)
    if not (EXACT | APPROVED_ANIMATIONS.keys()).issubset(paths):raise ValueError("Quellpaket unvollstaendig")
    # git archive liest nur versionierte Inhalte, niemals lokale Caches.
    import tarfile
    archive=subprocess.check_output(["git","archive","--format=tar",commit,"--",*sorted(paths)],cwd=root)
    payloads={}
    with tarfile.open(fileobj=io.BytesIO(archive),mode="r:") as source:
        for member in source:
            if member.isdir():continue
            if not member.isfile() or member.name not in paths:raise ValueError("Unerwarteter Archiveintrag")
            payloads[member.name]=scan(member.name,source.extractfile(member).read())
    if set(payloads)!=set(paths):raise ValueError("Fehlender Archiveintrag")
    if (DEMO_DOC in payloads)!=(DEMO_IMAGE in payloads):raise ValueError("Incomplete demonstration")
    manifest={"schema":1,"commit":commit,"kind":"source-review",
              "files":[{"path":name,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}
                       for name,data in sorted(payloads.items())]}
    payloads["SOURCE_MANIFEST.json"]=(json.dumps(manifest,indent=2)+"\n").encode()
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(dir=destination.parent,prefix=".source-export-") as temp:
        staged=Path(temp)/"source.zip"
        with zipfile.ZipFile(staged,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
            for name,data in sorted(payloads.items()):
                info=zipfile.ZipInfo(PREFIX+"/"+name,(2020,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                output.writestr(info,data)
        with zipfile.ZipFile(staged) as result:
            if result.testzip() is not None:raise ValueError("ZIP CRC Fehler")
            for name,data in payloads.items():
                if result.read(PREFIX+"/"+name)!=data:raise ValueError("ZIP Datenabweichung")
        verify_export(staged)
        # Hardlink publiziert die fertig gepruefte Datei ohne Ueberschreiben.
        # Ziel und temporaere Datei liegen auf demselben Volume.
        destination.hardlink_to(staged)
    return manifest


def verify_export(destination):
    """Read-only archive check, usable without Git or extraction."""
    manifest_name=PREFIX+"/SOURCE_MANIFEST.json"
    with zipfile.ZipFile(destination) as archive:
        entries=archive.infolist()
        if not 1<=len(entries)<=2048:
            raise ValueError("Invalid archive entry count")
        names=[item.filename for item in entries]
        if len({name.casefold() for name in names})!=len(names):
            raise ValueError("Duplicate archive entry")
        if manifest_name not in names:
            raise ValueError("Missing source manifest")
        total=0
        for item in entries:
            name=item.filename
            path=PurePosixPath(name)
            mode=(item.external_attr >> 16) & 0o170000
            if (item.is_dir() or mode not in (0,0o100000) or item.flag_bits & 1
                    or not name.startswith(PREFIX+"/") or "\\" in name
                    or path.as_posix()!=name or any(part in (".","..") for part in path.parts)
                    or ":" in name):
                raise ValueError("Unsafe archive entry")
            if not 0<item.file_size<=32*1024*1024:
                raise ValueError("Invalid archive entry size")
            total+=item.file_size
            if total>128*1024*1024:
                raise ValueError("Archive size limit exceeded")
        if archive.getinfo(manifest_name).file_size>2*1024*1024:
            raise ValueError("Manifest size limit exceeded")
        manifest=json.loads(archive.read(manifest_name))
        if (not isinstance(manifest,dict) or type(manifest.get("schema")) is not int or manifest.get("schema")!=1
                or manifest.get("kind")!="source-review"
                or not isinstance(manifest.get("commit"),str)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",manifest["commit"])
                or not isinstance(manifest.get("files"),list)):
            raise ValueError("Invalid source manifest")
        expected=set()
        for item in manifest["files"]:
            if not isinstance(item,dict):
                raise ValueError("Invalid file record")
            name=item.get("path")
            size=item.get("size")
            digest=item.get("sha256")
            if (not isinstance(name,str) or name in expected or not selected(name)
                    or type(size) is not int or size<=0
                    or not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest)):
                raise ValueError("Invalid file record")
            expected.add(name)
            full=PREFIX+"/"+name
            if full not in names or archive.getinfo(full).file_size!=size:
                raise ValueError("Source file missing or wrong size")
            data=scan(name,archive.read(full))
            if hashlib.sha256(data).hexdigest()!=digest:
                raise ValueError("Source file hash mismatch")
        if set(names)!={PREFIX+"/"+name for name in expected}|{manifest_name}:
            raise ValueError("Unexpected archive entry")
        if (DEMO_DOC in expected)!=(DEMO_IMAGE in expected):
            raise ValueError("Incomplete demonstration")
        if not (EXACT | APPROVED_ANIMATIONS.keys()).issubset(expected):
            raise ValueError("Source archive incomplete")
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination",type=Path)
    parser.add_argument("--verify",action="store_true",help="Verify existing ZIP without Git or extraction")
    args=parser.parse_args()
    result=verify_export(args.destination) if args.verify else make_export(ROOT,args.destination)
    print("SOURCE_VERIFY_OK" if args.verify else "SOURCE_EXPORT_OK",result["commit"],len(result["files"]))


if __name__=="__main__":main()
