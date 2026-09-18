$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "native-env.ps1")

Initialize-NativeBuildEnvironment

Push-Location $projectRoot
try {
    pnpm --filter @content-factory/desktop tauri dev
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri development process failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
