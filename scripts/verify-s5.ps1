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

Write-Host "S5 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "docs\specs\S5-script-studio.md",
    "docs\migrations\script-package-1.0.0-to-1.1.0.md",
    "packages\contracts\schemas\script-package.schema.json",
    "packages\contracts\schemas\script-task.schema.json",
    "services\api\src\content_factory_api\s5.py",
    "services\api\src\content_factory_api\s5_generation.py",
    "services\api\src\content_factory_api\s5_queue.py",
    "services\api\src\content_factory_api\s5_scripts.py",
    "services\api\src\content_factory_api\s5_sources.py",
    "apps\desktop\src\S5ScriptWorkspace.tsx",
    "apps\desktop\src\ScriptEditor.tsx",
    "data\s5\README.md"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S5 path is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S4 full regression" { pnpm verify:s4 }
    Invoke-CheckedCommand "S5 contracts, sources, queue, scripts, API and actual HTTP relays" {
        $env:CONTENT_FACTORY_S5_INTEGRATION = "1"
        try {
            & $python -m pytest packages\contracts\tests services\api\tests\test_s5_scripts.py services\api\tests\test_s5_gateway_integration.py
        }
        finally {
            Remove-Item Env:CONTENT_FACTORY_S5_INTEGRATION -ErrorAction SilentlyContinue
        }
    }
    Invoke-CheckedCommand "Script package 1.1 contract fixture" {
        & $python -m content_factory_contracts validate --kind script --file packages\contracts\fixtures\script.valid.json
    }
    Invoke-CheckedCommand "Script task contract fixture" {
        & $python -m content_factory_contracts validate --kind script_task --file packages\contracts\fixtures\script-task.valid.json
    }
    Invoke-CheckedCommand "Python dependency integrity" { & $python -m pip check }
    Invoke-CheckedCommand "Desktop S5 tests, types and production build" {
        pnpm --filter @content-factory/desktop test
        pnpm --filter @content-factory/desktop typecheck
        pnpm --filter @content-factory/desktop build
    }
}
finally {
    Pop-Location
}

Write-Host "`nS5 engineering verification passed. One real human-approved script package remains a separate business acceptance gate." -ForegroundColor Green
