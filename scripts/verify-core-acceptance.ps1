[CmdletBinding()]
param(
    [string]$RunRoot = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$storageRootName = "Codex" + [char]0x5DE5 + [char]0x4F5C + [char]0x76D8
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path "E:\" $storageRootName))
if ([string]::IsNullOrWhiteSpace($RunRoot)) {
    $RunRoot = Join-Path $allowedRoot "temp\content-factory-core-acceptance-final"
}
$runRootPath = [System.IO.Path]::GetFullPath($RunRoot)
if (-not $runRootPath.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RunRoot must stay under the configured E: workspace storage root: $runRootPath"
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$desktopRoot = Join-Path $projectRoot "apps\desktop"
$contractsRoot = Join-Path $projectRoot "packages\contracts"
$desktopBin = Join-Path $desktopRoot "node_modules\.bin"
$contractsBin = Join-Path $contractsRoot "node_modules\.bin"
$webOutput = Join-Path $runRootPath "web-dist"

foreach ($required in @(
    $python,
    (Join-Path $desktopBin "vitest.CMD"),
    (Join-Path $desktopBin "tsc.CMD"),
    (Join-Path $desktopBin "vite.CMD"),
    (Join-Path $contractsBin "vitest.CMD"),
    (Join-Path $contractsBin "tsc.CMD")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required local runtime is missing: $required"
    }
}

New-Item -ItemType Directory -Force -Path $runRootPath | Out-Null
$env:TEMP = Join-Path $runRootPath "temp"
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $runRootPath "pycache"
$env:CONTENT_FACTORY_RUNTIME_ROOT = Join-Path $runRootPath "runtime"
$env:CONTENT_FACTORY_MEDIA_ROOT = Join-Path $runRootPath "media"
$env:CONTENT_FACTORY_MEDIA_TEMP_ROOT = Join-Path $runRootPath "media-command-logs"
$env:CONTENT_FACTORY_ANALYSIS_ROOT = Join-Path $runRootPath "analysis"
$env:CONTENT_FACTORY_S3_CONFIG_PATH = Join-Path $runRootPath "gateway\settings.json"
$env:CONTENT_FACTORY_S4_DATA_DIR = Join-Path $runRootPath "s4"
$env:CONTENT_FACTORY_S5_DATA_DIR = Join-Path $runRootPath "s5"
$env:CONTENT_FACTORY_S6_DATA_DIR = Join-Path $runRootPath "s6"
$env:CONTENT_FACTORY_S7_DATA_DIR = Join-Path $runRootPath "s7"
$env:CONTENT_FACTORY_S8_DATA_DIR = Join-Path $runRootPath "s8"
$env:CONTENT_FACTORY_S2_INTEGRATION = "1"
$env:CONTENT_FACTORY_S3_INTEGRATION = "1"
$env:CONTENT_FACTORY_S5_INTEGRATION = "1"
$env:CONTENT_FACTORY_S6_INTEGRATION = "1"
$env:CONTENT_FACTORY_S7_INTEGRATION = "1"
$env:CONTENT_FACTORY_S8_INTEGRATION = "1"
$env:PYTHONPATH = @(
    (Join-Path $projectRoot "packages\contracts\python"),
    (Join-Path $projectRoot "workers\media\src"),
    (Join-Path $projectRoot "services\api\src")
) -join ";"
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

Write-Host "[1/7] Python contracts, API, queues, and FFmpeg integration"
& $python -m pytest `
    (Join-Path $projectRoot "packages\contracts\tests") `
    (Join-Path $projectRoot "workers\media\tests") `
    (Join-Path $projectRoot "services\api\tests") `
    --basetemp (Join-Path $runRootPath "pytest") `
    -o "cache_dir=$(Join-Path $runRootPath 'pytest-cache')" -q
if ($LASTEXITCODE -ne 0) { throw "Python acceptance failed" }

Write-Host "[2/7] Desktop behavior tests"
Push-Location $desktopRoot
try {
    & (Join-Path $desktopBin "vitest.CMD") run --cache=false
    if ($LASTEXITCODE -ne 0) { throw "Desktop behavior tests failed" }

    Write-Host "[3/7] Desktop typecheck"
    & (Join-Path $desktopBin "tsc.CMD") -p tsconfig.app.json --noEmit --pretty false
    if ($LASTEXITCODE -ne 0) { throw "Desktop application typecheck failed" }
    & (Join-Path $desktopBin "tsc.CMD") -p tsconfig.node.json --noEmit --pretty false
    if ($LASTEXITCODE -ne 0) { throw "Desktop build-config typecheck failed" }

    Write-Host "[4/7] Desktop production web build"
    & (Join-Path $desktopBin "vite.CMD") build --outDir $webOutput --emptyOutDir
    if ($LASTEXITCODE -ne 0) { throw "Desktop production web build failed" }
} finally {
    Pop-Location
}

Write-Host "[5/7] TypeScript contract tests"
Push-Location $contractsRoot
try {
    & (Join-Path $contractsBin "vitest.CMD") run --cache=false
    if ($LASTEXITCODE -ne 0) { throw "TypeScript contract tests failed" }

    Write-Host "[6/7] TypeScript contract typecheck"
    & (Join-Path $contractsBin "tsc.CMD") -p tsconfig.json --noEmit --pretty false
    if ($LASTEXITCODE -ne 0) { throw "TypeScript contract typecheck failed" }
} finally {
    Pop-Location
}

Write-Host "[7/7] Core acceptance complete"
Write-Host "All checks passed. Isolated run root: $runRootPath"
