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
$SpecFile = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "OfflineWritingReviser.spec"))
$IconFile = Join-Path $ProjectRoot "src\offline_writing_reviser\assets\offline-writing-reviser.ico"
$EntryPoint = Join-Path $ProjectRoot "src\offline_writing_reviser\__main__.py"
$ExpectedExecutable = Join-Path $OutputDir "OfflineWritingReviser.exe"

foreach ($Target in @($OutputDir, $BuildDir)) {
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
if (Test-Path -LiteralPath $SpecFile) {
    Remove-Item -LiteralPath $SpecFile -Force
}

& $Python -c "import PyInstaller, PIL, pystray"
if ($LASTEXITCODE -ne 0) {
    throw 'Required build tooling is missing. Run: python -m pip install -e ".[dev,build]"'
}

if (-not (Test-Path -LiteralPath $IconFile -PathType Leaf)) {
    & $Python (Join-Path $ProjectRoot "scripts\generate_icon.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Application icon generation failed."
    }
}

if (-not $SkipTests) {
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed; packaging stopped."
    }
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name OfflineWritingReviser `
    --icon $IconFile `
    --add-data "$IconFile;assets" `
    --exclude-module gi `
    --exclude-module matplotlib `
    --exclude-module numpy `
    --exclude-module psutil `
    --exclude-module PyQt5 `
    --exclude-module PySide6 `
    --exclude-module tkinter.test `
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

Write-Host "Offline Writing Reviser build created: $ExpectedExecutable"
