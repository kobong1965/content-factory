$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)] [string]$Label,
        [Parameter(Mandatory = $true)] [scriptblock]$Command
    )
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Write-Host "S3 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "docs\specs\S3-deep-analysis.md",
    "packages\contracts\schemas\ocr-result.schema.json",
    "packages\contracts\schemas\analysis-task.schema.json",
    "workers\media\src\content_factory_media\ocr.py",
    "services\api\src\content_factory_api\s3.py",
    "services\api\src\content_factory_api\s3_gateway.py",
    "apps\desktop\src\S3AnalysisWorkspace.tsx",
    "data\analysis-validation\manifest.json"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S3 path is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S2 full regression" { pnpm verify:s2 }
    Invoke-CheckedCommand "Deterministic OCR fixture" { & $python scripts\create-s3-ocr-fixture.py }

    $env:CONTENT_FACTORY_S3_INTEGRATION = "1"
    try {
        Invoke-CheckedCommand "S3 contracts, DPAPI, queue, API, OCR and actual HTTP gateway" {
            & $python -m pytest packages\contracts\tests workers\media\tests services\api\tests
        }
    }
    finally {
        Remove-Item Env:CONTENT_FACTORY_S3_INTEGRATION -ErrorAction SilentlyContinue
    }

    Invoke-CheckedCommand "OCR result contract fixture" {
        & $python -m content_factory_contracts validate --kind ocr_result --file packages\contracts\fixtures\ocr-result.valid.json
    }
    Invoke-CheckedCommand "Analysis task contract fixture" {
        & $python -m content_factory_contracts validate --kind analysis_task --file packages\contracts\fixtures\analysis-task.valid.json
    }
    Invoke-CheckedCommand "Analysis report 1.1 contract fixture" {
        & $python -m content_factory_contracts validate --kind analysis --file packages\contracts\fixtures\analysis.valid.json
    }
    Invoke-CheckedCommand "Python dependency integrity" { & $python -m pip check }
    Invoke-CheckedCommand "Desktop S3 tests, types and production build" {
        pnpm --filter @content-factory/desktop test
        pnpm --filter @content-factory/desktop typecheck
        pnpm --filter @content-factory/desktop build
    }
}
finally {
    Pop-Location
}

Write-Host "`nS3 engineering verification passed. Live relay quality and 20 real analyses remain separate acceptance gates." -ForegroundColor Green
