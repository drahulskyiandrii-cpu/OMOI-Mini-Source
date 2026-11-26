# OMOI Mini

OMOI Mini ist ein Desktop-Sprachassistent für Windows mit animierter 3D-Figur.
Das Projekt entstand im Rahmen einer Umschulung zum Fachinformatiker für Systemintegration.

## Funktionen

- Text- und Spracheingabe auf Deutsch und Russisch.
- Unabhängige Auswahl von Spracherkennung (STT), Sprachmodell (LLM) und Sprachausgabe (TTS).
- Cloud-Dienste (OpenAI, Cartesia) und separat eingerichtete lokale Whisper-, Gemma- und Qwen-Komponenten.
- Austauschbarer VRM-Avatar mit 27 lizenzierten VRMA-Animationen.
- Lokale SQLite-Datenbank für Einstellungen, Chatverlauf, Notizen und Timer.
- Manuelles Vorwärmen und bedarfsgerechter Start lokaler Modelle; keine automatischen Modell-Downloads.

## Architektur

```text
python/
  omoi_mini/       Anwendung, Controller, Datenbank und Adapter
  tests/           Automatisierte Tests
web/
  src/             JavaScript, CSS, Avatar und Benutzeroberfläche
  index.html       HTML-Oberfläche
local-stt/         Separate Worker für lokale Spracherkennung
local-tts/         Separate Worker für lokale Sprachausgabe
assets/            Standardavatar, Animationen und Lizenzhinweise
scripts/           Start-, Build-, Test- und Paketwerkzeuge
installer/         Konfiguration der Windows-Installer
docs/              Architektur, Installation und Lizenzen
```

Datenfluss: Mikrofon → STT → Controller/LLM → TTS → Audio.
Bei Texteingabe entfällt die Spracherkennung. Qt WebChannel verbindet die Python-Anwendung mit der Three.js-Oberfläche. Notizen und Timer funktionieren auch ohne KI-Dienste.

## Installation und Start

Voraussetzungen: Windows 11 x64, Python 3.11 x64 und Node.js 24.
Für den Client werden 16 GB RAM empfohlen; lokale Modelle benötigen gegebenenfalls mehr Speicher.

PowerShell im Quellordner:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
npm ci --prefix web
npm run build --prefix web
.\start.bat
```

Zugangsdaten ausschließlich als Umgebungsvariablen konfigurieren. Lokale Modelle und Worker werden separat eingerichtet. Details: [Installation](docs/INSTALLATION.md).

## Dokumentation

- [Demonstration](docs/DEMO.md)
- [Architektur](docs/SCHOOL_ARCHITECTURE.md)
- [Installation und Windows-Paket](docs/INSTALLATION.md)
- [Windows-Installer](docs/INSTALLER.md)
- [Standardavatar](assets/default-avatar/README.md)
- [Animationen](assets/default-animations/README.md)
- [Datenschutz und Weitergabe](docs/DISTRIBUTION.md)
- [Lizenzen](docs/LICENSES.md)
- [Entwicklungshinweise](AGENTS.md)

## Tests

```powershell
$env:PYTHONPATH = "$PWD\python"
.\.venv\Scripts\python.exe -m pytest -q python/tests
node --test web/animation_policy.test.mjs web/animation_variants.test.mjs web/default_animations.test.mjs web/emotion_motion.test.mjs web/face_motion.test.mjs web/motion_blend.test.mjs local-stt/acoustic_worker.test.mjs
```

Letzte vollständige Regression: 706 Python-Tests bestanden, 3 übersprungen; 25 JavaScript-Tests bestanden.
Installation und Start aus einer neu eingerichteten Python- und Node-Umgebung sowie die Windows-Installer wurden geprüft. Der Funktionstest umfasst die Oberfläche, den Avatar, alle 27 Animationsrouten, Einstellungen, Katalog, Diagnose, Notizen und Timer.

## Projektstatus und Lizenz

Windows-Prototyp für Lern- und Demonstrationszwecke. Die Hardwareprüfung der aktuellen lokalen STT-/LLM-/TTS-Kette und die Einrichtung externer Modelllaufzeiten auf einem neuen Zielsystem stehen noch aus.

Der eigene Quellcode steht unter der [MIT-Lizenz](LICENSE). Drittanbieter und Ressourcen behalten ihre jeweiligen Lizenzen. Modellgewichte, persönliche Sprachreferenzen, Zugangsdaten und Benutzerdaten sind nicht enthalten. Die öffentliche Weitergabe des gebündelten Windows-Clients setzt die vollständige Erfüllung der Drittanbieter-Lizenzbedingungen voraus.
