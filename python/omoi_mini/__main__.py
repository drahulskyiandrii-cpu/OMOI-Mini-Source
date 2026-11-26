"""Start: PYTHONPATH=python python -m omoi_mini."""
from pathlib import Path
from .gui import start_gui

if __name__=="__main__":
    project=Path(__file__).resolve().parents[2]
    raise SystemExit(start_gui(project/"web"/"dist"))
