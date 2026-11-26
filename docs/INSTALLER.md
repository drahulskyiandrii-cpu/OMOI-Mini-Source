# Windows-Installer bauen

Voraussetzungen: Inno Setup 6.7 oder neuer, vollstaendiger
gepruefter OneDir-Build (EXE und _internal), MIT-Lizenz sowie
vollstaendige Drittanbieter-Lizenzunterlagen im Paket.

Der Installer installiert pro Benutzer ohne Administratorrechte.
Benutzerdaten liegen getrennt in LOCALAPPDATA und werden bei der
Deinstallation nicht absichtlich geloescht. Keine Modelle oder
API-Schluessel werden mitgeliefert.

Beispiel (PowerShell, Pfade anpassen):

```powershell
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'   /DPayload="C:\Build\OMOI-Mini" /DOutputFolder="C:\Build\Installer" installer\OMOI-Mini.iss
```

Bei einer Installation von Inno Setup pro Benutzer liegt ISCC.exe
stattdessen unter LOCALAPPDATA\Programs\Inno Setup 6.
Der erzeugte Setup-EXE ist ohne separates Code-Signing nicht
digital signiert. Nicht oeffentlich verteilen, bis alle Rechte
und die Installations-/Deinstallationspruefung geklaert sind.

## Lokaler Vorschau-Installer (keine Freigabe)

Der Schalter `-WithoutBundledAssets` beim Windows-Build erzeugt ein
Paket ohne VRM und VRMA. Die Daten des Testprofils bleiben ausserhalb
des Pakets. Fuer einen getrennten Test-Installer kann ISCC mit
`/DLocalPreview` aufgerufen werden. Das Payload-Verzeichnis muss dann
zusaetzlich einen lokalen `START-LOCAL-PREVIEW.bat` enthalten, der das
Testprofil explizit setzt. Der Vorschau-Installer hat eine andere App-ID
und ein eigenes Installationsverzeichnis. Er ist kein oeffentliches
Release.

## Quellcode-Installer mit separater Runtime

OMOI-Mini-Source-Setup-x64.exe installiert den geprueften Quellcode,
die gebaute Webansicht und die lizenzierten Standardressourcen, jedoch
keine Qt-, Python- oder KI-Binaerdateien. Voraussetzung: Python 3.11 x64
mit Python Launcher und Internetzugang zu PyPI. Beim ersten Start
installiert START-INSTALLED.bat die versionsgebundene Runtime aus
requirements-runtime-lock.txt direkt von PyPI in eine eigene .venv.
Keine Node-Installation fuer die bereits gebaute Webansicht erforderlich.
Das Installationsverzeichnis ist vom OneDir-Installer getrennt.
Beim Deinstallieren wird nur die lokale Runtime entfernt; Benutzerdaten
bleiben ausserhalb des Installationsordners. Der Quellcode-Installer
vermeidet die eigene Weitergabe von Qt-Wheels, nicht die Lizenzbedingungen
der installierten Abhaengigkeiten. OneDir bleibt ein lokaler Kandidat
bis alle Binaerweitergabepflichten nachgewiesen sind. Inno Setup-Lizenz
bei kommerzieller Nutzung separat beachten.
