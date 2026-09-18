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
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

Write-Host "S8 verification: $projectRoot" -ForegroundColor Green
$requiredPaths = @(
    "docs\specs\S8-publishing-feedback.md",
    "packages\contracts\schemas\publication.schema.json",
    "packages\contracts\schemas\metric-snapshot.schema.json",
    "packages\contracts\schemas\metric-import-draft.schema.json",
    "packages\contracts\schemas\learning-report.schema.json",
    "services\api\src\content_factory_api\s8.py",
    "services\api\src\content_factory_api\s8_store.py",
    "services\api\src\content_factory_api\s8_imports.py",
    "services\api\src\content_factory_api\s8_learning.py",
    "apps\desktop\src\S8FeedbackWorkspace.tsx",
    "apps\desktop\src\PublicationRegistry.tsx",
    "apps\desktop\src\MetricCapturePanel.tsx",
    "apps\desktop\src\PerformanceReviewPanel.tsx",
    "apps\desktop\src\BusinessClosureCard.tsx",
    "data\s8\README.md"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) { throw "Required S8 path is missing: $relativePath" }
}
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment missing. Create .venv and install the editable local packages first." }

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S0-S7 full regression" { pnpm verify:s7 }
    Invoke-CheckedCommand "Create deterministic metric screenshot" { & $python scripts\create-s8-metric-fixture.py }
    $env:CONTENT_FACTORY_S8_INTEGRATION = "1"
    try {
        Invoke-CheckedCommand "S8 contracts, store, import, API, learning, and actual OCR" {
            & $python -m pytest packages\contracts\tests services\api\tests\test_s8_feedback.py services\api\tests\test_s8_integration.py
        }
    }
    finally { Remove-Item Env:CONTENT_FACTORY_S8_INTEGRATION -ErrorAction SilentlyContinue }
    foreach ($fixture in @(
        @{ Kind = "publication"; File = "publication.valid.json" },
        @{ Kind = "metric_snapshot"; File = "metric-snapshot.valid.json" },
        @{ Kind = "metric_import_draft"; File = "metric-import-draft.valid.json" },
        @{ Kind = "learning_report"; File = "learning-report.valid.json" }
    )) {
        Invoke-CheckedCommand "Validate $($fixture.Kind) fixture" {
            & $python -m content_factory_contracts validate --kind $fixture.Kind --file (Join-Path "packages\contracts\fixtures" $fixture.File)
        }
    }
    Invoke-CheckedCommand "Python dependency integrity" { & $python -m pip check }
    Invoke-CheckedCommand "Desktop tests, types and production build" {
        pnpm --filter @content-factory/desktop test
        pnpm --filter @content-factory/desktop typecheck
        pnpm --filter @content-factory/desktop build
    }
    Invoke-CheckedCommand "Windows native compile" {
        . (Join-Path $PSScriptRoot "native-env.ps1")
        Initialize-NativeBuildEnvironment
        pnpm --filter @content-factory/desktop tauri build --debug --no-bundle
    }
}
finally { Pop-Location }

Write-Host "`nS8 engineering verification passed. One real published work, two confirmed snapshots, and explicit business review confirmation remain a separate business gate." -ForegroundColor Green
