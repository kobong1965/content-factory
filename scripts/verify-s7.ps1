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

Write-Host "S7 verification: $projectRoot" -ForegroundColor Green
$requiredPaths = @(
    "docs\specs\S7-auto-editing.md",
    "packages\contracts\schemas\edit-project.schema.json",
    "packages\contracts\schemas\render-task.schema.json",
    "packages\contracts\schemas\render-output.schema.json",
    "packages\contracts\schemas\edit-audio-asset.schema.json",
    "services\api\src\content_factory_api\s7.py",
    "services\api\src\content_factory_api\s7_projects.py",
    "services\api\src\content_factory_api\s7_store.py",
    "services\api\src\content_factory_api\s7_queue.py",
    "services\api\src\content_factory_api\s7_renderer.py",
    "apps\desktop\src\S7EditingWorkspace.tsx",
    "apps\desktop\src\EditTimeline.tsx",
    "apps\desktop\src\FinishedVideoPanel.tsx",
    "data\s7\README.md"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) { throw "Required S7 path is missing: $relativePath" }
}
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment missing. Create .venv and install the editable local packages first." }

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S0-S6 full regression" { pnpm verify:s6 }
    $env:CONTENT_FACTORY_S7_INTEGRATION = "1"
    try {
        Invoke-CheckedCommand "S7 contracts, store, queue, API and actual FFmpeg delivery" {
            & $python -m pytest packages\contracts\tests services\api\tests\test_s7_editing.py services\api\tests\test_s7_integration.py
        }
    }
    finally { Remove-Item Env:CONTENT_FACTORY_S7_INTEGRATION -ErrorAction SilentlyContinue }
    foreach ($fixture in @(
        @{ Kind = "edit_project"; File = "edit-project.valid.json" },
        @{ Kind = "render_task"; File = "render-task.valid.json" },
        @{ Kind = "render_output"; File = "render-output.valid.json" },
        @{ Kind = "edit_audio_asset"; File = "edit-audio-asset.valid.json" }
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

Write-Host "`nS7 engineering verification passed. One real approved output remains a separate business gate." -ForegroundColor Green
