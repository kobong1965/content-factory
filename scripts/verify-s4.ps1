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

Write-Host "S4 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "docs\specs\S4-product-knowledge.md",
    "docs\migrations\product-profile-1.0.0-to-1.1.0.md",
    "packages\contracts\schemas\product-profile.schema.json",
    "services\api\src\content_factory_api\s4.py",
    "services\api\src\content_factory_api\s4_products.py",
    "services\api\src\content_factory_api\s4_store.py",
    "apps\desktop\src\S4ProductWorkspace.tsx",
    "apps\desktop\src\ProductEditor.tsx",
    "data\s4\README.md"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S4 path is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S3 full regression" { pnpm verify:s3 }
    Invoke-CheckedCommand "S4 contract, store, API and file acceptance" {
        & $python -m pytest packages\contracts\tests services\api\tests\test_s4_products.py
    }
    Invoke-CheckedCommand "Product profile 1.1 contract fixture" {
        & $python -m content_factory_contracts validate --kind product --file packages\contracts\fixtures\product.valid.json
    }
    Invoke-CheckedCommand "Python dependency integrity" { & $python -m pip check }
    Invoke-CheckedCommand "Desktop S4 tests, types and production build" {
        pnpm --filter @content-factory/desktop test
        pnpm --filter @content-factory/desktop typecheck
        pnpm --filter @content-factory/desktop build
    }
}
finally {
    Pop-Location
}

Write-Host "`nS4 engineering verification passed. Three real products remain a separate business acceptance gate." -ForegroundColor Green
