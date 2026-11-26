# Build a source-based installer; only runtime downloads happen on the target PC.
param([string] $OutputFolder = "", [string] $Compiler = "")
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (!$OutputFolder) { $OutputFolder = Join-Path $root "dist\installer" }
if (!$Compiler) { $Compiler = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe" }
if (!(Test-Path $python) -or !(Test-Path $Compiler)) { throw "Build environment or Inno Setup compiler missing." }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("omoi-mini-source-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $tmp | Out-Null
try {
    Push-Location $root
    try {
        if (git status --porcelain) { throw "Commit the reviewed source before creating its installer." }
        npm run build --prefix web
        if ($LASTEXITCODE -ne 0) { throw "Web build failed." }
        $zip = Join-Path $tmp "source.zip"
        & $python (Join-Path $root "scripts\export_source.py") $zip
        if ($LASTEXITCODE -ne 0) { throw "Source export failed." }
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp
        $payload = Join-Path $tmp "OMOI-Mini"
        Copy-Item (Join-Path $root "web\dist") (Join-Path $payload "web\dist") -Recurse
        $notices = Join-Path $payload "Third-party-web-licenses"
        New-Item -ItemType Directory $notices | Out-Null
        $modules = Join-Path $root "web\node_modules"
        $packages = @((Join-Path $modules "three")) + @(Get-ChildItem (Join-Path $modules "@pixiv") -Directory | Select-Object -ExpandProperty FullName)
        foreach ($package in $packages) {
            $license = Join-Path $package "LICENSE"
            if (!(Test-Path $license -PathType Leaf)) { throw "Missing web dependency license: $package" }
            $name = Split-Path $package -Leaf
            Copy-Item $license (Join-Path $notices ($name + "-LICENSE.txt"))
        }
        $blocked = @(Get-ChildItem $payload -File -Force -Recurse | Where-Object {
            $_.Extension -in @(".dll",".exe",".pyd",".sqlite",".sqlite3",".gguf",".wav",".onnx",".safetensors")
        })
        if ($blocked.Count) { throw "Native runtime or private asset in source payload." }
        & $Compiler "/DPayload=$payload" "/DOutputFolder=$OutputFolder" (Join-Path $root "installer\OMOI-Mini-Source.iss")
        if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
    } finally { Pop-Location }
} finally {
    if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}
