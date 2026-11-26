"""Stage only the reviewed worker sources in a Windows OneDir payload."""
from pathlib import Path
import sys
import shutil
from omoi_mini.worker_bundle import FILES

def stage_workers(source: Path, target: Path):
    if not (target / "OMOI-Mini.exe").is_file():
        raise ValueError("Not a Windows payload")
    for rel in FILES:
        src=source / rel
        dst=target / rel
        if not src.is_file() or src.is_symlink() or src.parent.is_symlink():
            raise ValueError("Missing or linked worker: "+rel)
        if not 0 < src.stat().st_size <= 2*1024*1024:
            raise ValueError("Worker size invalid: "+rel)
        if dst.is_symlink() or dst.parent.is_symlink():
            raise ValueError("Linked destination: "+rel)
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
        if dst.read_bytes()!=src.read_bytes():
            raise ValueError("Worker verification failed: "+rel)
    return len(FILES)

if __name__=="__main__":
    print("WORKERS_STAGED",stage_workers(Path(sys.argv[1]),Path(sys.argv[2])))
