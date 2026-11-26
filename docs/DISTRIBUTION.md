# Weitergabe und Datenschutz

Das kompakte Quellpaket enthaelt gepruefte Git-Dateien aus einer
festen Auswahl, keine Arbeitskopie mit ignorierten Dateien.
Die benoetigten Abhaengigkeiten werden separat installiert.
Eine manifestierte SHA-256-Liste dient dem Integritaetsvergleich,
nicht als digitale Signatur.

Das Windows-Paket enthaelt den Client samt benoetigter Bibliotheken,
aber keine KI-Gewichte, privaten VRM/VRMA oder Sprachreferenzen. Der
explizit freigegebene Standardavatar und 27 lizenzierte Apache-2.0-Animationen
liegen getrennt in assets/ mit Lizenzhinweisen.
Externe Worker befinden sich im Quellpaket.

**Freigabestatus:** privates Demonstrations-/Pruefpaket, kein
oeffentlich freigegebener Release. Eigener Projektcode: MIT (siehe LICENSE). Drittanbieter behalten ihre jeweiligen
Lizenzbedingungen. Vor oeffentlicher Verteilung muessen insbesondere
Qt/PySide6, npm-Bibliotheken, PyInstaller und weitere mitgelieferte
Bibliotheken, Modellrechte und Asset-Rechte geprueft werden.
Ein bestandener Dateiaudit ersetzt keine Lizenzpruefung.
Keine Garantie der Lauffaehigkeit auf einem anderen PC.

In einem Git-Checkout dieses Quellrepos kann der gepruefte Quellstand
reproduzierbar exportiert werden: `python scripts/export_source.py <neue-zip-datei>`.
Nur Dateien des aktuellen Commits werden gelesen. Nicht versionierte Dateien und die Git-Historie sind nicht enthalten.

Ein vorhandenes ZIP laesst sich ohne Git und ohne Entpacken pruefen:

```powershell
python scripts/export_source.py --verify <zip-datei>
```

Die Pruefung kontrolliert SHA-256, Dateiliste, Groessen, Pfade und
freigegebene Ressourcen. Das Manifest ist keine digitale Signatur;
es ersetzt nicht den Vergleich mit einem vertrauenswuerdigen Git-Stand.

Das Quellarchiv enthaelt auch die kurze Demonstration mit genau einem
freigegebenen, per SHA-256 gepinnten Screenshot des Standardavatars.
Keine anderen Screenshots werden automatisch exportiert.
