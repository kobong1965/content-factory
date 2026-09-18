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

Write-Host "S6 verification: $projectRoot" -ForegroundColor Green
$requiredPaths = @(
    "docs\specs\S6-material-library.md",
    "packages\contracts\schemas\material-asset.schema.json",
    "packages\contracts\schemas\shooting-task.schema.json",
    "packages\contracts\schemas\material-import-task.schema.json",
    "packages\contracts\schemas\material-usage.schema.json",
    "services\api\src\content_factory_api\s6.py",
    "services\api\src\content_factory_api\s6_queue.py",
    "services\api\src\content_factory_api\s6_materials.py",
    "services\api\src\content_factory_api\s6_recognition.py",
    "services\api\src\content_factory_api\s6_matching.py",
    "services\api\src\content_factory_api\s6_store.py",
    "apps\desktop\src\S6MaterialWorkspace.tsx",
    "apps\desktop\src\MaterialEditor.tsx",
    "apps\desktop\src\ShootingTaskPanel.tsx",
    "data\s6\README.md"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S6 path is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S0-S5 full regression" { pnpm verify:s5 }
    Invoke-CheckedCommand "Create deterministic real video fixture" {
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create-s2-fixture.ps1
    }
    $env:CONTENT_FACTORY_S6_INTEGRATION = "1"
    try {
        Invoke-CheckedCommand "S6 contracts, queue, store, actual FFmpeg and two actual HTTP relays" {
            & $python -m pytest packages\contracts\tests services\api\tests\test_s6_materials.py services\api\tests\test_s6_integration.py
        }
    }
    finally {
        Remove-Item Env:CONTENT_FACTORY_S6_INTEGRATION -ErrorAction SilentlyContinue
    }
    foreach ($fixture in @(
        @{ Kind = "material"; File = "material.valid.json" },
        @{ Kind = "shooting_task"; File = "shooting-task.valid.json" },
        @{ Kind = "material_import_task"; File = "material-import-task.valid.json" },
        @{ Kind = "material_usage"; File = "material-usage.valid.json" }
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
finally {
    Pop-Location
}

Write-Host "`nS6 engineering verification passed. One real approved script fully matched with real materials remains a separate business gate." -ForegroundColor Green
