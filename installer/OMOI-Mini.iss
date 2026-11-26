#ifndef Payload
  #error Payload must point to a complete audited OneDir build
#endif
#ifndef OutputFolder
  #define OutputFolder "..\dist\installer"
#endif
#ifdef LocalPreview
  #define MiniAppId "OMOI-Mini.AndriiDrahulskyi.Preview"
  #define MiniAppName "OMOI Mini Preview"
  #define MiniOutput "OMOI-Mini-Preview-Setup-x64"
#else
  #define MiniAppId "OMOI-Mini.AndriiDrahulskyi"
  #define MiniAppName "OMOI Mini"
  #define MiniOutput "OMOI-Mini-Setup-x64"
#endif
[Setup]
AppId={#MiniAppId}
AppName={#MiniAppName}
AppVersion=0.1.0
AppPublisher=Andrii Drahulskyi
DefaultDirName={localappdata}\Programs\{#MiniAppName}
DefaultGroupName={#MiniAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputFolder}
OutputBaseFilename={#MiniOutput}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\OMOI-Mini.exe
CloseApplications=yes
RestartApplications=no
[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "START-LOCAL-PREVIEW.bat"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef LocalPreview
Source: "{#Payload}\START-LOCAL-PREVIEW.bat"; DestDir: "{app}"; Flags: ignoreversion
#endif
[Icons]
#ifdef LocalPreview
Name: "{group}\{#MiniAppName}"; Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-LOCAL-PREVIEW.bat"""; WorkingDir: "{app}"; IconFilename: "{app}\OMOI-Mini.exe"
Name: "{autodesktop}\{#MiniAppName}"; Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-LOCAL-PREVIEW.bat"""; WorkingDir: "{app}"; IconFilename: "{app}\OMOI-Mini.exe"; Tasks: desktopicon
#else
Name: "{group}\OMOI Mini"; Filename: "{app}\OMOI-Mini.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\OMOI Mini"; Filename: "{app}\OMOI-Mini.exe"; WorkingDir: "{app}"; Tasks: desktopicon
#endif
[Run]
#ifdef LocalPreview
Filename: "{cmd}"; Parameters: "/d /c ""{app}\START-LOCAL-PREVIEW.bat"""; Description: "{cm:LaunchProgram,{#MiniAppName}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked
#else
Filename: "{app}\OMOI-Mini.exe"; Description: "{cm:LaunchProgram,OMOI Mini}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked
#endif
