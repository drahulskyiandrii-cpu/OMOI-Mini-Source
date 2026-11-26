"""OMOI Mini – Windows-Start für Python und PyInstaller.

Im gepackten Zustand liegen Web-Assets unter sys._MEIPASS/web/dist.
Persönliche Daten und Modellgewichte bleiben außerhalb des EXE-Pakets.
"""
from pathlib import Path
import sys

from omoi_mini.gui import start_gui


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web" / "dist"
    return Path(__file__).resolve().parents[1] / "web" / "dist"


if __name__ == "__main__":
    raise SystemExit(start_gui(resource_dir()))
