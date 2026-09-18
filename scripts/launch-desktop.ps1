[CmdletBinding()]
param(
    [switch]$PrintDataProfileId
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$applicationPath = Join-Path $projectRoot "dist\windows\content-factory\content-factory-desktop.exe"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$apiStartedHere = $false
$apiProcess = $null
$apiCompatVersion = "0.1.0"

function Get-ContentFactoryInstanceId {
    param([Parameter(Mandatory = $true)][string]$Root)

    $normalized = [System.IO.Path]::GetFullPath($Root).TrimEnd([char[]]@('\', '/')).ToLowerInvariant()
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
        $hex = [System.BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
        return $hex.Substring(0, 16)
    }
    finally {
        $sha256.Dispose()
    }
}

$projectInstanceId = Get-ContentFactoryInstanceId -Root $projectRoot
$projectBuildId = "content-factory-" + ((Get-Content -LiteralPath (Join-Path $projectRoot "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version)
$applicationDataFolder = -join [char[]]@(0x7206, 0x6B3E, 0x5185, 0x5BB9, 0x5DE5, 0x5382)
$productionDataProfile = [ordered]@{
    CONTENT_FACTORY_RUNTIME_ROOT = Join-Path $projectRoot "data\runtime"
    CONTENT_FACTORY_MEDIA_ROOT = Join-Path $projectRoot "data\media"
    CONTENT_FACTORY_ANALYSIS_ROOT = Join-Path $projectRoot "data\analysis"
    CONTENT_FACTORY_S3_CONFIG_PATH = Join-Path (Join-Path $env:LOCALAPPDATA $applicationDataFolder) "gateway-config.json"
    CONTENT_FACTORY_S4_DATA_DIR = Join-Path $projectRoot "data\s4"
    CONTENT_FACTORY_S5_DATA_DIR = Join-Path $projectRoot "data\s5"
    CONTENT_FACTORY_S6_DATA_DIR = Join-Path $projectRoot "data\s6"
    CONTENT_FACTORY_S7_DATA_DIR = Join-Path $projectRoot "data\s7"
    CONTENT_FACTORY_S8_DATA_DIR = Join-Path $projectRoot "data\s8"
}

function Get-ContentFactoryDataProfileId {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Paths)

    $normalized = foreach ($path in $Paths.Values) {
        [System.IO.Path]::GetFullPath([string]$path).TrimEnd([char[]]@('\', '/')).ToLowerInvariant()
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]::Join("`n", $normalized))
        $hex = [System.BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
        return $hex.Substring(0, 16)
    }
    finally {
        $sha256.Dispose()
    }
}

$dataProfileId = Get-ContentFactoryDataProfileId -Paths $productionDataProfile

if ($PrintDataProfileId) {
    Write-Output $dataProfileId
    exit 0
}

function Test-ContentFactoryApi {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8766/health" -TimeoutSec 1
        return (
            $health -is [psobject] -and
            $health.service -eq "api" -and
            $health.status -eq "ok" -and
            $health.version -eq $apiCompatVersion -and
            $health.instance_id -eq $projectInstanceId -and
            $health.data_profile_id -eq $dataProfileId -and
            $health.build_id -eq $projectBuildId
        )
    }
    catch { return $false }
}

if (-not (Test-Path -LiteralPath $applicationPath)) { throw "Content Factory executable is missing: $applicationPath" }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Local service runtime is missing: $pythonPath" }

$existingApplication = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -eq $applicationPath } catch { $false }
} | Select-Object -First 1
if ($null -ne $existingApplication) { exit 0 }

try {
    if (-not (Test-ContentFactoryApi)) {
        foreach ($entry in $productionDataProfile.GetEnumerator()) {
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([System.IO.Path]::GetFullPath([string]$entry.Value))
        }
        Remove-Item Env:CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS -ErrorAction SilentlyContinue
        Remove-Item Env:CONTENT_FACTORY_S7_INTEGRATION -ErrorAction SilentlyContinue
        $env:PYTHONPATH = @(
            (Join-Path $projectRoot "packages\contracts\python"),
            (Join-Path $projectRoot "workers\media\src"),
            (Join-Path $projectRoot "services\api\src")
        ) -join ";"
        $apiProcess = Start-Process -FilePath $pythonPath `
            -ArgumentList @("-m", "uvicorn", "content_factory_api.main:app", "--host", "127.0.0.1", "--port", "8766", "--log-level", "warning") `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        $apiStartedHere = $true
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
            Start-Sleep -Milliseconds 250
            if ($apiProcess.HasExited) { throw "Local service failed to start" }
            if (Test-ContentFactoryApi) {
                $ready = $true
                break
            }
        }
        if (-not $ready) { throw "Local service startup timed out" }
    }

    $application = Start-Process -FilePath $applicationPath -WorkingDirectory (Split-Path -Parent $applicationPath) -PassThru
    $application.WaitForExit()
}
finally {
    if ($apiStartedHere -and $null -ne $apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id
    }
}
