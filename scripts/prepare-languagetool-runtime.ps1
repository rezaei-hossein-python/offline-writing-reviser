param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ManifestPath = Join-Path $ProjectRoot "runtime-manifest.json"
$VendorRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "vendor"))
$JavaTarget = [System.IO.Path]::GetFullPath((Join-Path $VendorRoot "java"))
$LanguageToolTarget = [System.IO.Path]::GetFullPath((Join-Path $VendorRoot "languagetool"))
$DownloadRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".runtime-download"))

foreach ($Path in @($VendorRoot, $JavaTarget, $LanguageToolTarget, $DownloadRoot)) {
    if (
        -not $Path.StartsWith(
            $ProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Runtime path escapes the project root: $Path"
    }
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Runtime manifest is missing: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json

function Get-VerifiedArchive {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$ExpectedSha256
    )

    $Archive = Join-Path $DownloadRoot $Name
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        Write-Host "Downloading $Name"
        Invoke-WebRequest -Uri $Url -OutFile $Archive -UseBasicParsing
    }
    $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Checksum mismatch for $Name. Expected $ExpectedSha256, received $Actual"
    }
    return $Archive
}

function Expand-SingleRootArchive {
    param(
        [Parameter(Mandatory=$true)][string]$Archive,
        [Parameter(Mandatory=$true)][string]$Target,
        [Parameter(Mandatory=$true)][string]$StageName
    )

    $Stage = Join-Path $DownloadRoot $StageName
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Stage -Force
    $Roots = @(Get-ChildItem -LiteralPath $Stage -Directory)
    if ($Roots.Count -ne 1) {
        throw "Expected one root directory in $Archive; found $($Roots.Count)"
    }
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    Move-Item -LiteralPath $Roots[0].FullName -Destination $Target
    Remove-Item -LiteralPath $Stage -Recurse -Force
}

$JavaReady = Test-Path -LiteralPath (Join-Path $JavaTarget "bin\javaw.exe") -PathType Leaf
$LanguageToolReady = Test-Path -LiteralPath (Join-Path $LanguageToolTarget "languagetool-server.jar") -PathType Leaf

function Remove-LanguageToolDevelopmentArtifacts {
    $DevelopmentArtifacts = @(
        (Join-Path $LanguageToolTarget "testrules.bat"),
        (Join-Path $LanguageToolTarget "testrules.sh"),
        (Join-Path $LanguageToolTarget "libs\languagetool-core-tests.jar")
    )
    foreach ($Artifact in $DevelopmentArtifacts) {
        if (Test-Path -LiteralPath $Artifact) {
            Remove-Item -LiteralPath $Artifact -Force
        }
    }
}

if ($JavaReady -and $LanguageToolReady -and -not $Force) {
    Remove-LanguageToolDevelopmentArtifacts
    Write-Host "Private Java and LanguageTool runtimes are already prepared."
    exit 0
}

New-Item -ItemType Directory -Path $VendorRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DownloadRoot -Force | Out-Null

$JavaArchive = Get-VerifiedArchive `
    -Name "temurin-jre-17-windows-x64.zip" `
    -Url $Manifest.java.url `
    -ExpectedSha256 $Manifest.java.sha256
$LanguageToolArchive = Get-VerifiedArchive `
    -Name "LanguageTool-6.6.zip" `
    -Url $Manifest.languagetool.url `
    -ExpectedSha256 $Manifest.languagetool.sha256

Expand-SingleRootArchive -Archive $JavaArchive -Target $JavaTarget -StageName "java-stage"
Expand-SingleRootArchive -Archive $LanguageToolArchive -Target $LanguageToolTarget -StageName "languagetool-stage"
Remove-LanguageToolDevelopmentArtifacts

$Required = @(
    (Join-Path $JavaTarget "bin\javaw.exe"),
    (Join-Path $JavaTarget "legal"),
    (Join-Path $LanguageToolTarget "languagetool-server.jar"),
    (Join-Path $LanguageToolTarget "COPYING.txt"),
    (Join-Path $LanguageToolTarget "third-party-licenses")
)
foreach ($Path in $Required) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Prepared runtime is incomplete: $Path"
    }
}

Write-Host "Prepared Eclipse Temurin $($Manifest.java.version) under $JavaTarget"
Write-Host "Prepared LanguageTool $($Manifest.languagetool.version) under $LanguageToolTarget"
