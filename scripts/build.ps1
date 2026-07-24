param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OutputDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"

if (-not (Test-Path $Python)) {
    $Python = "python"
}

if (-not $SkipTests) {
    & $Python -m pytest
}

$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
$ResolvedBuild = [System.IO.Path]::GetFullPath($BuildDir)
$ResolvedProject = [System.IO.Path]::GetFullPath($ProjectRoot)

if (-not $ResolvedOutput.StartsWith($ResolvedProject)) {
    throw "Refusing to clean output outside project root: $ResolvedOutput"
}
if (-not $ResolvedBuild.StartsWith($ResolvedProject)) {
    throw "Refusing to clean build outside project root: $ResolvedBuild"
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --name OfflineWritingReviser `
    --paths (Join-Path $ProjectRoot "src") `
    (Join-Path $ProjectRoot "src\offline_writing_reviser\__main__.py")

Write-Host "Offline Writing Reviser build created under $OutputDir"
