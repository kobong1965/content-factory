$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

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

Write-Host "S1 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "docs\specs\S1-contracts-and-gold-set.md",
    "packages\contracts\schemas\analysis-report.schema.json",
    "packages\contracts\schemas\product-profile.schema.json",
    "packages\contracts\schemas\script-package.schema.json",
    "packages\contracts\schemas\gold-set-case.schema.json",
    "packages\contracts\schemas\gold-set-manifest.schema.json",
    "packages\contracts\python\content_factory_contracts\validation.py",
    "data\gold-set\manifest.json"
)

foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S1 path is missing: $relativePath"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S0 regression" { pnpm verify:s0 }
    Invoke-CheckedCommand "S1 Python contracts and API tests" {
        & $python -m pytest packages\contracts\tests services\api\tests workers\media\tests
    }
    Invoke-CheckedCommand "Analysis fixture" {
        & $python -m content_factory_contracts validate --kind analysis --file packages\contracts\fixtures\analysis.valid.json
    }
    Invoke-CheckedCommand "Product fixture" {
        & $python -m content_factory_contracts validate --kind product --file packages\contracts\fixtures\product.valid.json
    }
    Invoke-CheckedCommand "Script fixture and references" {
        & $python -m content_factory_contracts validate --kind script `
            --file packages\contracts\fixtures\script.valid.json `
            --related-product packages\contracts\fixtures\product.valid.json `
            --related-analysis packages\contracts\fixtures\analysis.valid.json
    }
    Invoke-CheckedCommand "Gold-set case fixture" {
        & $python -m content_factory_contracts validate --kind gold_case --file packages\contracts\fixtures\gold-case.valid.json
    }
    Invoke-CheckedCommand "Real gold-set manifest" {
        & $python -m content_factory_contracts validate --kind gold_manifest --file data\gold-set\manifest.json
    }
    Invoke-CheckedCommand "Honest gold-set status" {
        & $python -m content_factory_contracts gold-set-status --manifest data\gold-set\manifest.json
    }
}
finally {
    Pop-Location
}

Write-Host "`nS1 engineering verification passed. Real business data remains governed by the manifest." -ForegroundColor Green
