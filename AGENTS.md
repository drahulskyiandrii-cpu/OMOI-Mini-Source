# Entwicklungshinweise

## Architektur

Die Python-Anwendung befindet sich in `python/omoi_mini/`, die Weboberfläche in `web/src/`. Externe Sprach-Worker befinden sich in `local-stt/` und `local-tts/`. Die Build-Skripte liegen in `scripts/`.

## Entwicklungsstandards

- Die Anwendung muss ohne Abhängigkeit von anderen Projekt-Checkouts funktionieren.
- Anbieterwechsel, Abbruchlogik, Turn-IDs, Audio-Reihenfolge und Thread-Grenzen bleiben konsistent.
- Keine stillen Wechsel zwischen lokalen und Cloud-Anbietern.
- Fehlermeldungen enthalten keine Zugangsdaten oder privaten Dateipfade.
- Tests verwenden synthetische Daten und simulierte Anbieter. Echte API-Aufrufe und Modellstarts sind getrennte Integrationstests, die eine ausdrueckliche Freigabe erfordern.
- Änderungen durch Regressionstests absichern; Qt-Tests können mit `QT_QPA_PLATFORM=offscreen` ausgeführt werden.
- Zugangsdaten, private Ressourcen, Modellgewichte, lokale Laufzeitumgebungen und Build-Artefakte gehören nicht in Git.
- Neue Abhängigkeiten und Ressourcen auf Lizenzbedingungen prüfen.

Siehe [Architektur](docs/SCHOOL_ARCHITECTURE.md), [Installation](docs/INSTALLATION.md) und [Lizenzen](docs/LICENSES.md).
