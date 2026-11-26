#ifndef Payload
  #error Reviewed source export and prebuilt web UI required
#endif
#ifndef OutputFolder
  #define OutputFolder "..\dist\installer"
#endif
[Setup]
AppId=OMOI-Mini.AndriiDrahulskyi.Source
AppName=OMOI Mini
AppVersion=0.1.0
AppPublisher=Andrii Drahulskyi
DefaultDirName={localappdata}\Programs\OMOI Mini Source
DefaultGroupName=OMOI Mini Source
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputFolder}
OutputBaseFilename=OMOI-Mini-Source-Setup-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\OMOI Mini"; Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-INSTALLED.bat"""; WorkingDir: "{app}"
Name: "{autodesktop}\OMOI Mini Source"; Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-INSTALLED.bat"""; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-INSTALLED.bat"""; Description: "Install runtime packages and start OMOI Mini"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked
[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: files; Name: "{app}\runtime-ready.txt"
