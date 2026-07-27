param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

$OutputDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
$BuildDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$TempDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".build-temp"))
$SpecFile = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "OfflineWritingReviser.spec"))
$IconFile = Join-Path $ProjectRoot "src\offline_writing_reviser\assets\offline-writing-reviser.ico"
$EntryPoint = Join-Path $ProjectRoot "src\offline_writing_reviser\__main__.py"
$ExpectedApplicationDir = Join-Path $OutputDir "OfflineWritingReviser"
$ExpectedExecutable = Join-Path $ExpectedApplicationDir "OfflineWritingReviser.exe"
$JavaRuntime = Join-Path $ProjectRoot "vendor\java"
$LanguageToolRuntime = Join-Path $ProjectRoot "vendor\languagetool"
$ThirdPartyNotices = Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md"

foreach ($Target in @($OutputDir, $BuildDir, $TempDir)) {
    if (
        -not $Target.StartsWith(
            $ProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing to clean directory outside project root: $Target"
    }
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
$env:TEMP = $TempDir
$env:TMP = $TempDir
if (Test-Path -LiteralPath $SpecFile) {
    Remove-Item -LiteralPath $SpecFile -Force
}

& $Python -c "import PyInstaller, PIL, PySide6"
if ($LASTEXITCODE -ne 0) {
    throw 'Required build tooling is missing. Run: python -m pip install -e ".[dev,build]"'
}

if (-not (Test-Path -LiteralPath $IconFile -PathType Leaf)) {
    & $Python (Join-Path $ProjectRoot "scripts\generate_icon.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Application icon generation failed."
    }
}
foreach ($RequiredPath in @(
    (Join-Path $JavaRuntime "bin\javaw.exe"),
    (Join-Path $LanguageToolRuntime "languagetool-server.jar"),
    $ThirdPartyNotices
)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "Required private runtime or notice file is missing: $RequiredPath"
    }
}

if (-not $SkipTests) {
    & $Python -m pytest -p no:cacheprovider --basetemp (Join-Path $TempDir "pytest")
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed; packaging stopped."
    }
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --contents-directory app `
    --windowed `
    --name OfflineWritingReviser `
    --icon $IconFile `
    --add-data "$IconFile;assets" `
    --add-data "$JavaRuntime;runtime\java" `
    --add-data "$LanguageToolRuntime;runtime\languagetool" `
    --add-data "$ThirdPartyNotices;licenses" `
    --exclude-module gi `
    --exclude-module matplotlib `
    --exclude-module numpy `
    --exclude-module psutil `
    --exclude-module PyQt5 `
    --exclude-module tkinter `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.Qt3DRender `
    --exclude-module PySide6.QtBluetooth `
    --exclude-module PySide6.QtCharts `
    --exclude-module PySide6.QtDataVisualization `
    --exclude-module PySide6.QtLocation `
    --exclude-module PySide6.QtMultimedia `
    --exclude-module PySide6.QtNetworkAuth `
    --exclude-module PySide6.QtPdf `
    --exclude-module PySide6.QtPositioning `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtRemoteObjects `
    --exclude-module PySide6.QtScxml `
    --exclude-module PySide6.QtSensors `
    --exclude-module PySide6.QtSerialPort `
    --exclude-module PySide6.QtSql `
    --exclude-module PySide6.QtStateMachine `
    --exclude-module PySide6.QtTest `
    --exclude-module PySide6.QtWebChannel `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtWebSockets `
    --paths (Join-Path $ProjectRoot "src") `
    --distpath $OutputDir `
    --workpath $BuildDir `
    --specpath $ProjectRoot `
    $EntryPoint
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

if (-not (Test-Path -LiteralPath $ExpectedExecutable -PathType Leaf)) {
    throw "Build finished without expected executable: $ExpectedExecutable"
}

if (Test-Path -LiteralPath $TempDir) {
    Remove-Item -LiteralPath $TempDir -Recurse -Force
}

Write-Host "Offline Writing Reviser build created: $ExpectedExecutable"
