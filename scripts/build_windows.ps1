# OMOI Mini: reproduzierbarer One-Directory-Windows-Build (ohne Modelle/Secrets).
# Verwendung: powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
param(
    [switch] $Windowed,
    [switch] $WithoutBundledAssets
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "web\dist\index.html"
if (-not (Test-Path $python)) { throw "Python-venv fehlt" }
if (-not (Test-Path $frontend)) { throw "Web-Build fehlt: npm run build --prefix web" }
$mode = if ($Windowed) { "--windowed" } else { "--console" }
# Erst VRMA-Struktur, Rollen, Quellen, explizite Redistributierung und SHA-256
# verifizieren. Bei fehlender Freigabe VOR PyInstaller sicher abbrechen.
$assetArguments = @()
if (-not $WithoutBundledAssets) {
    $approvedAnimations = Join-Path $root "assets\default-animations"
    & $python (Join-Path $root "scripts\validate_public_animations.py") --assets $approvedAnimations
    if ($LASTEXITCODE -ne 0) { throw "Öffentliche VRMA-Lizenzprüfung fehlgeschlagen" }
    $clips = @(Get-ChildItem -LiteralPath $approvedAnimations -Filter "*.vrma" -File -ErrorAction SilentlyContinue)
    if ($clips.Count -gt 0) {
        $assetArguments = @("--add-data", ($approvedAnimations + ";assets/default-animations"))
    }
    $avatarDir = Join-Path $root "assets\default-avatar"
    if (Test-Path (Join-Path $avatarDir "default.vrm")) {
        $env:PYTHONPATH = Join-Path $root "python"
        & $python -c "from pathlib import Path; import sys; from omoi_mini.default_avatar import approved_default_avatar; assert approved_default_avatar(Path(sys.argv[1]))" $avatarDir
        if ($LASTEXITCODE -ne 0) { throw "Default avatar validation failed" }
        $assetArguments += @("--add-data", ($avatarDir + ";assets/default-avatar"))
    }
} else {
    Write-Output 'Asset-free build: user resources remain outside the package.'
}
$arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", $mode,
    "--name", "OMOI-Mini",
    "--paths", (Join-Path $root "python"),
    "--additional-hooks-dir", (Join-Path $root "scripts\pyinstaller-hooks"),
    "--add-data", ((Join-Path $root "web\dist") + ";web/dist")
) + $assetArguments + @(
    "--distpath", (Join-Path $root "dist\windows"),
    "--workpath", (Join-Path $root "build\pyinstaller-work"),
    "--specpath", (Join-Path $root "build\pyinstaller-spec"),
    (Join-Path $root "scripts\omoi_mini_entry.py")
)
Push-Location $root
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller-Build fehlgeschlagen" }
    $exe = Join-Path $root "dist\windows\OMOI-Mini\OMOI-Mini.exe"
    if (-not (Test-Path $exe)) { throw "EXE wurde nicht gefunden" }
    # Include only our reviewed worker scripts, not their environments or model weights.
    $bundle = Split-Path -Parent $exe
    $env:PYTHONPATH = Join-Path $root "python"
    & $python (Join-Path $root "scripts\stage_workers.py") $root $bundle
    if ($LASTEXITCODE -ne 0) { throw "Local worker packaging failed" }
    # Remove known unused plugins only after checking all retained PE imports.
    & $python (Join-Path $root "scripts\prune_windows_qt.py") (Join-Path $root "dist\windows\OMOI-Mini")
    if ($LASTEXITCODE -ne 0) { throw "Qt-Paketbereinigung fehlgeschlagen" }
    # Read-only: Keine EXE, kein Qt, keine KI-Modelle starten.
    & $python (Join-Path $root "scripts\audit_portable_windows.py") --folder (Join-Path $root "dist\windows\OMOI-Mini")
    if ($LASTEXITCODE -ne 0) { throw "Portable-Dateiinventur fehlgeschlagen" }
    Write-Output "BUILD_OK: dist\windows\OMOI-Mini\OMOI-Mini.exe"
} finally {
    Pop-Location
}
