# Installation und Start

## 1. Windows-Paket (OneDir)

Das ZIP in einen eigenen Ordner entpacken. Den kompletten Ordner
OMOI-Mini behalten und OMOI-Mini.exe starten. Der Ordner _internal
ist erforderlich. Nicht nur die EXE kopieren.

Python und Node sind fuer den gepackten Client nicht erforderlich.
Fuer lokale Worker sind sie gegebenenfalls separat notwendig.
Das ZIP ist eine portable Ausgabe, nicht signiert und wurde
noch nicht auf einem unabhaengigen Windows-PC abgenommen.

Beim ersten Start wird der lizenzierte Standardavatar geladen. Eigene
VRM und VRMA lassen sich weiterhin importieren; gespeicherte Auswahlen
werden nicht ueberschrieben. Siehe [Avatar](../assets/default-avatar/README.md)
und [Animationen](../assets/default-animations/README.md).
Das Programm laedt beim Start keine KI-Modelle und sendet keine
kostenpflichtige Begruessungs-Synthese.

## 2. Quellcode

README-Schritte ausfuehren; Python 3.11 x64 und Node 24 verwenden.
Die versionsgebundenen Python- und npm-Dateien dokumentieren
die gepruefte Entwicklerumgebung. Internet ist fuer die
Paketinstallation erforderlich. Keine Modellgewichte werden
dadurch automatisch eingerichtet.

Start.bat setzt PYTHONPATH und startet die Python-Anwendung.
Fuer lokale Modelle und Stimmen die gewuenschten Pfade explizit
konfigurieren. OMOI_MINI_DEV_LEGACY ist eine interne Kompatibilitaetsoption
und bleibt bei eigenstaendigem Betrieb deaktiviert.

## 3. Cloud-Betrieb

Eigene Windows-Umgebungsvariablen OPENAI_API_KEY und
CARTESIA_API_KEY setzen. CARTESIA_VOICE_ID kann alternativ
in Mini eingestellt werden. Keine Werte in Git oder ZIP
aufnehmen. .env.example ist eine Liste der Variablennamen,
keine automatisch eingelesene Konfigurationsdatei.
API-Verfuegbarkeit und Kosten haengen vom eigenen Konto ab.
Bei Nichtnutzung kann Sprachausgabe deaktiviert werden.

## 4. Lokaler Betrieb (optional)

Unter Modelle die eigenen Dateien/Verzeichnisse waehlen.
Gemma: GGUF und aktuelle llama-server.exe mit Auto-fit-Unterstuetzung
(--fit, --fit-target, --n-gpu-layers auto). Die automatische Speicheranpassung zielt
auf 1536 MiB freie GPU-Speicherreserve; reale Werte sind noch zu pruefen.
Bei zusaetzlich ausgewaehltem GPU-STT/TTS wird die eigene LLM beim naechsten
Gebrauch freigegeben und anschliessend bei Bedarf mit Auto-fit gestartet.
Kein Modellstart allein durch Aendern einer Einstellung. Externe Prozesse
bleiben unveraendert; reale Hardwareabnahme steht noch aus.
OpenVINO: Node-Abhaengigkeiten mit npm ci --prefix local-stt,
eigenes kompatibles Whisper-Modellverzeichnis.
CUDA: eigenes Python, CT2-Modell und kompatible Bibliotheken.
Qwen/Piper: getrennte Python-Umgebungen, Modelle/Stimme.
Die externen Modellumgebungen muessen separat eingerichtet werden.
Die Installation dieser Umgebungen auf einem neuen Zielsystem ist
noch nicht vollstaendig geprueft. Keine automatischen Modell-Downloads.
Nach geaenderten Runtime-Pfaden kann Neustart erforderlich sein.
Manueller Start-Knopf laedt nur die ausgewaehlten lokalen Modelle.
Qwen-Startfehler und Abbruch waehrend des Starts geben den eigenen
Prozess frei. Bereits warme und fremde Dienste werden nicht automatisch
beendet. Bei HTTP 429 wartet der Warmup-Auftrag begrenzt auf die Freigabe.

### Referenzumgebungen fuer lokale Sprachmodelle

Die folgenden Versionen stammen aus der vorhandenen Windows-Testumgebung.
Sie sind keine vollstaendige Installations-Lockdatei und keine
allgemeine Hardwarefreigabe.

| Komponente | Version |
| --- | --- |
| Python | 3.11 |
| PyTorch / TorchAudio | 2.9.0+cu128 |
| Faster-Whisper | 1.2.1 |
| CTranslate2 | 4.9.0 |
| PyAV | 17.1.0 |
| Faster-Qwen3-TTS | 0.5.4 |
| qwen-tts-hf | 0.1.1.post1 |
| Transformers | 5.15.1 |
| Accelerate | 1.12.0 |
| FastAPI / Uvicorn | 0.162.5 / 0.54.0 |

CUDA-STT verwendet eine separate Faster-Whisper-Paketablage (Overlay)
zusammen mit einer Python-Runtime mit passenden CUDA-Bibliotheken.
Qwen benoetigt eine lokale Modellkopie sowie eine eigene freigegebene
Stimmreferenz. Die CF050-Variante erfordert zusaetzlich den exakt
passenden Adapter und die festgelegte Basismodellrevision.
Modelle und Adapter sind nicht Teil des Quellpakets. Vorhandene
Umgebungen wurden auf Paketkonsistenz geprueft; ein Neuaufbau
der Modellumgebungen und die reale Inferenz muessen separat getestet werden.

## 5. Build aus Quellen

Nach dem Web-Build:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -Windowed
```

Ausgabe: dist/windows/OMOI-Mini/OMOI-Mini.exe und _internal.
Der Build fuehrt den statischen Paket-Audit aus.
Die Anwendung wird dadurch nicht gestartet. Fuer einen Setup-EXE siehe [Installer bauen](INSTALLER.md).

## 6. Benutzerdaten

Standard: %LOCALAPPDATA%/OMOI-Mini, ausserhalb des Quellordners.
Fuer ein getrenntes Profil OMOI_MINI_DATA_DIR explizit setzen.
Keine bestehende Datenbank oder Modellumgebung loeschen.
