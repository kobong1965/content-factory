$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
. (Join-Path $PSScriptRoot "native-env.ps1")

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-Sha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

Write-Host "S0 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "AGENTS.md",
    "apps\desktop\src\App.tsx",
    "apps\desktop\src-tauri\tauri.conf.json",
    "packages\contracts\src\index.ts",
    "services\api\src\content_factory_api\main.py",
    "workers\media\src\content_factory_media\cli.py",
    "docs\source\SOURCE_MANIFEST.sha256"
)

foreach ($relativePath in $requiredPaths) {
    $fullPath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath)) {
        throw "Required S0 path is missing: $relativePath"
    }
}

Write-Host "`n[Source archive]" -ForegroundColor Cyan
$manifestPath = Join-Path $projectRoot "docs\source\SOURCE_MANIFEST.sha256"
$manifestLines = Get-Content -LiteralPath $manifestPath -Encoding UTF8 |
    Where-Object { $_.Trim().Length -gt 0 }

foreach ($line in $manifestLines) {
    if ($line -notmatch "^(?<hash>[a-f0-9]{64})  (?<path>.+)$") {
        throw "Invalid source manifest line: $line"
    }

    $sourcePath = Join-Path $projectRoot $Matches.path
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Archived source is missing: $($Matches.path)"
    }

    $actualHash = Get-Sha256 -Path $sourcePath
    if ($actualHash -ne $Matches.hash) {
        throw "Archived source hash mismatch: $($Matches.path)"
    }
}
Write-Host "Verified $($manifestLines.Count) archived source files."

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install API/media dev dependencies first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "Web typecheck, tests and build" { pnpm verify:web }
    Invoke-CheckedCommand "Python tests" { & $python -m pytest services\api\tests workers\media\tests }
    Invoke-CheckedCommand "Media worker health" { & $python -m content_factory_media --check }

    Write-Host "`n[Tauri configuration]" -ForegroundColor Cyan
    $tauriConfigPath = Join-Path $projectRoot "apps\desktop\src-tauri\tauri.conf.json"
    $tauriConfig = Get-Content -LiteralPath $tauriConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($tauriConfig.version -ne "0.1.16") {
        throw "Unexpected Tauri version: $($tauriConfig.version)"
    }
    if ($tauriConfig.identifier -ne "com.atai.contentfactory") {
        throw "Unexpected Tauri identifier: $($tauriConfig.identifier)"
    }

    Initialize-NativeBuildEnvironment
    $cargo = Get-Command cargo.exe -ErrorAction SilentlyContinue
    if ($null -eq $cargo) {
        throw "Rust toolchain missing: cargo.exe is required for the native Tauri check."
    }
    Invoke-CheckedCommand "Native Tauri compile check" {
        & $cargo.Source check --manifest-path apps\desktop\src-tauri\Cargo.toml
    }
}
finally {
    Pop-Location
}

Write-Host "`nS0 verification passed." -ForegroundColor Green
