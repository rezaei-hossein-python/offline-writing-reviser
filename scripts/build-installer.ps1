param(
    [switch]$SkipApplicationBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildScript = Join-Path $ProjectRoot "scripts\build.ps1"
$InstallerScript = Join-Path $ProjectRoot "installer\OfflineWritingReviser.iss"
$ApplicationExe = Join-Path $ProjectRoot "dist\OfflineWritingReviser\OfflineWritingReviser.exe"
$InstallerExe = Join-Path $ProjectRoot "dist\installer\OfflineWritingReviser-Setup.exe"

if (-not $SkipApplicationBuild) {
    & $BuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "Application build failed."
    }
}
if (-not (Test-Path -LiteralPath $ApplicationExe -PathType Leaf)) {
    throw "Packaged application is missing: $ApplicationExe"
}

$IsccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $Iscc = $Command.Source
    }
}
if (-not $Iscc) {
    throw "Inno Setup 6 is required. Install package JRSoftware.InnoSetup."
}

& $Iscc $InstallerScript
if ($LASTEXITCODE -ne 0) {
    throw "Installer build failed."
}
if (-not (Test-Path -LiteralPath $InstallerExe -PathType Leaf)) {
    throw "Installer build finished without expected output: $InstallerExe"
}

$Hash = Get-FileHash -LiteralPath $InstallerExe -Algorithm SHA256
$ChecksumPath = "$InstallerExe.sha256"
"$($Hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($InstallerExe))" |
    Set-Content -LiteralPath $ChecksumPath -Encoding ascii

Get-Item -LiteralPath $InstallerExe | Select-Object FullName,Length,LastWriteTime
Write-Host "SHA-256: $($Hash.Hash)"
Write-Host "Checksum: $ChecksumPath"
