# Lizenzen

Der eigene OMOI-Mini-Quellcode steht unter der MIT-Lizenz (siehe
[LICENSE](../LICENSE)). Drittanbieter behalten ihre eigenen Rechte.

| Komponente | Lizenz laut installierten Paketmetadaten |
| --- | --- |
| PySide6, Shiboken6, Qt | LGPL-3.0 / GPL-2.0 / GPL-3.0 (je nach Komponente) |
| Qt WebEngine / Chromium | verschiedene Drittanbieter-Lizenzen |
| Three.js, @pixiv/three-vrm, @pixiv/three-vrm-animation | MIT |
| httpx, httpcore, idna | BSD-3-Clause |
| anyio, h11, sounddevice | MIT |
| cffi | MIT-0 |
| NumPy | BSD-3-Clause und weitere gebundelte Lizenzen |
| certifi | MPL-2.0 |
| typing_extensions, Python | PSF-Lizenz |
| PyInstaller | GPL mit Bootloader-Ausnahme |

Die Tabelle ist eine Orientierung, keine vollstaendige Freigabe des
Windows-Binaerpakets. Abhaengigkeiten werden beim Quellcode-Build
separat installiert. Fuer eine Weitergabe des gebuendelten Clients
sind die Original-Lizenztexte, Drittanbieter-Hinweise, die passenden
Quellcode-/Relinking-Pflichten und Qt-WebEngine-/Chromium-Hinweise
vollstaendig zu erfuellen. Siehe
[Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)
und [Qt WebEngine licensing](https://doc.qt.io/qt-6/qtwebengine-licensing.html).

Der Windows-Build verzichtet auf den nicht benoetigten kompletten
QML-Modulbaum. Ein gezielter Post-Build-Schritt prueft normale und
verzoegerte PE-Imports aller verbleibenden Binaerdateien und entfernt
die ungenutzten Plugins der virtuellen Tastatur und des Quick3D-Profilers
mit den ausschliesslich von ihnen benoetigten Qt-Bibliotheken.
Dies ersetzt keine vollstaendige Lizenzinventur. Der konkrete Build
muss weiterhin auf Modullizenzen und LGPL-/Chromium-Pflichten geprueft
werden, bevor er oeffentlich verteilt wird.
[Qt-Modullizenzen](https://doc.qt.io/qt-6/licensing.html).

KI-Modelle, Stimmen und private Avatare sind nicht Teil der MIT-Freigabe.
Der [Standardavatar](../assets/default-avatar/README.md) bleibt unter
CC0-1.0. Die [Overte-basierten Animationen](../assets/default-animations/README.md)
stehen separat unter Apache-2.0.

## Gepruefter Stand 26.11.2026

Die installierten PySide6-/Shiboken-Pakete 6.12.0 und die separaten
Pdf-/WebEngine-Pakete 6.12.0.140 nennen in METADATA LGPL-3.0-only OR
GPL-2.0-only OR GPL-3.0-only. Die mitgelieferte License-File-Angabe
verweist jedoch nur auf LicenseRef-Qt-Commercial.txt. Die PyPI-Seiten
der exakten Versionen bestaetigen die OSS-Lizenzoptionen. Der Dateiname
allein belegt daher keine kommerzielle Exklusivlizenz.

Die sechs Windows-x64-Wheels wurden erneut von PyPI bezogen und anhand
der dort veroeffentlichten SHA-256-Werte geprueft. 4021 Dateien der
installierten Pakete (ohne RECORD) und 350 gleichnamige Dateien des
OneDir-Builds stimmen bytegenau mit diesen Wheels ueberein. Neun
zusaetzliche MSVC-DLLs unter PySide6 und Shiboken6 sind nicht durch
diesen Vergleich abgedeckt.

Qt meldet WebEngine 6.140.0, Chromium 140.0.7339.225 und Sicherheitsstand
152.0.7977.83. Die Release Notes des eigenstaendigen Qt-WebEngine-Releases
vom 13.11.2026 nennen dagegen Sicherheitskorrekturen bis
157.0.8103.50. Der spaetere Quellstand ist daher nicht ohne weiteren
Nachweis als korrespondierender Quellcode dieses Wheels geeignet.

Vor Binaerweitergabe offen: exakte korrespondierende Quellen,
Modullizenzen, vollstaendige Lizenztexte und Drittanbieterhinweise
(Chromium, PDFium, FFmpeg), Austauschbarkeit und MSVC-Weitergaberechte.
Qt-SBOM als Inventurgrundlage verwenden. Die Quellinstallation bezieht
die Runtime-Pakete separat von PyPI.
Inno Setup 6.7.3 wurde in der nichtkommerziellen Edition verwendet.

- https://pypi.org/project/PySide6/6.12.0/
- https://pypi.org/project/PySide6-WebEngine/6.12.0.140/
- https://doc.qt.io/qt-6.12/licensing.html
- https://doc.qt.io/qtforpython-6/overviews/qtwebengine-licensing.html
- https://www.qt.io/development/open-source-lgpl-obligations

Weitere Referenzen:
- https://www.qt.io/blog/qt-for-python-release-6.12-is-here
- https://code.qt.io/cgit/qt/qtreleasenotes.git/about/qtwebengine/6.140.0/release-note.md
- https://doc.qt.io/qt-6.12/sbom.html
- https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files
