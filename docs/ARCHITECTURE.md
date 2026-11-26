# OMOI Mini - Systemarchitektur

Das Programm verbindet eine kleine 3D-Figur mit einem Sprachassistenten.
Python steuert die Anwendung, JavaScript stellt die Figur dar.

| Datei / Klasse | Aufgabe |
|---|---|
| __main__.py / scripts/omoi_mini_entry.py | Start im Quellcode / Windows-Paket |
| gui.py: start_gui, MiniWindow, Bridge | Qt-Fenster, Verbindung zwischen JavaScript und Python |
| web/index.html, web/src/main.js | Bedienelemente, 3D-Figur, Animationen und Status |
| recorder.py: Recorder.start, stop | Mikrofon aufnehmen, WAV-Daten bereitstellen |
| controller.py: Controller.listen_audio, _transcribe | Audio an den gewaehlten STT-Anbieter senden |
| local_stt.py: LocalWhisper.transcribe | Externen lokalen STT-Prozess ansprechen |
| providers.py: transcribe_openai | Alternative STT in der Cloud |
| controller.py: _dispatch_text, _process, _stream_llm | Text verarbeiten, Antwort und Verlauf steuern |
| providers.py: LocalGemma.stream_answer, cloud_answer_stream | Lokale oder externe Textgenerierung |
| streaming.py: SpeechChunker | Antwort in sprechbare Abschnitte teilen |
| controller.py: _speak | TTS-Anbieter waehlen, Audio-Cache verwenden |
| audio_cache.py / pcm_wave.py | Gueltige Audio-Dateien wiederverwenden und fehlerhafte Daten erkennen |
| pcm_resample.py | Mikrofon-Samples bei Bedarf auf 16 kHz umrechnen |
| local_tts.py / piper_tts.py / providers.py | Qwen, Piper oder Cartesia ansprechen |
| audio.py: AudioPlayer | WAV und PCM abspielen, Lippenbewegung signalisieren |
| core.py: Store.get_settings, set_setting | Einstellungen, Nachrichten und Notizen in SQLite |
| controller.py: prewarm_stack, _stack_tasks | Lokale Komponenten bewusst nacheinander starten |
| runtime_config.py | Konfigurierte Laufzeitpfade aufloesen |
| providers.py: LocalGemma.ensure | Speicherbewusster llama.cpp-Start, begrenzter Wiederholungsversuch |

## Ablauf
Mikrofon -> Recorder -> STT -> Text -> LLM -> Textabschnitte -> TTS -> Audio.
Bei Tastatureingabe entfaellt STT. Lokale Werkzeuge und vorbereitete
Antworten koennen das LLM umgehen.

## Warum Threads und Warteschlangen?
Die Oberflaeche soll beim Warten auf ein Modell bedienbar bleiben.
Ein STT-Executor verarbeitet Erkennung, ein serieller TTS-Executor
erhaelt die Reihenfolge der Sprachausgabe. Turn-IDs und ein Lock
schuetzen den Gespraechszustand vor alten Ergebnissen. Qt-Signale
uebertragen Status und Audio an die Oberflaeche.

Beim Stoppen erhaelt die Anwendung eine neue Turn-ID. Ein alter
Worker darf dann keine Antwort mehr speichern oder abspielen.
Die gleiche Aufnahme darf nur einmal als finaler STT-Auftrag
in die Warteschlange gelangen. Die erste TTS-Phrase kann als
PCM-Stream frueh starten; weitere Phrasen liegen als WAV-Dateien
in der Wiedergabereihenfolge. So bleiben weniger grosse Audio-
Puffer gleichzeitig in der Oberflaeche.

## Einstellungen und Fehler
Anbieter werden unabhaengig gewaehlt; kein heimlicher Cloud-Wechsel.
Der Startknopf laedt nur ausgewaehlte lokale Modelle, nicht beim
Programmstart. Geaenderte lokale Laufzeitpfade koennen einen Neustart
erfordern. Fehler werden angezeigt, ohne geheime Daten auszugeben.
Die aktuelle technische Abnahme ersetzt keinen echten Mikrofontest.

## Speicherverwaltung lokaler Modelle
Bei kaltem Start werden ausgewaehlte GPU-Sprachkomponenten zuerst vorbereitet.
llama.cpp waehlt anschliessend die GPU-Layer automatisch mit einer Zielreserve
von 1536 MiB. Bei erkanntem Speichermangel ist ein weiterer Startversuch
mit 3072 MiB Reserve moeglich. Ein bereits ausgegebener Text wird nicht
automatisch wiederholt. Bei zusaetzlicher GPU-Sprachauswahl wird die
eigene LLM erst bei Gebrauch freigegeben und bedarfsgerecht neu geladen.
Die reale Speicherreserve ist hardwareabhaengig.

## Tests
Automatisierte Python- und JavaScript-Tests pruefen die Adapter und Zustandslogik.
Die Tests verwenden unter anderem kuenstliche Audiodaten und
simulierte HTTP-Antworten. Eine bestandene Simulation bedeutet
noch nicht, dass die echte Spracherkennung fehlerfrei ist.
