[CmdletBinding()]
param(
    [string]$OutputRoot = 'E:\Codex工作盘\artifacts\test-builds\creator-desktop-build',
    [string]$CargoTarget = 'E:\Codex工作盘\caches\cargo-target-content-factory-0.1.9'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = Join-Path $repo 'apps\desktop'
$native = Join-Path $desktop 'src-tauri'
$allowed = [IO.Path]::GetFullPath('E:\Codex工作盘\')
foreach ($target in @($OutputRoot, $CargoTarget)) {
    if (-not [IO.Path]::GetFullPath($target).StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay in E:\Codex工作盘' }
}
if (Test-Path -LiteralPath $OutputRoot) { throw 'Use a fresh OutputRoot; existing artifacts are preserved.' }
$webOutput = Join-Path $OutputRoot 'web-dist'
$env:CARGO_HOME = 'E:\Codex工作盘\caches\cargo-home'
$env:CARGO_TARGET_DIR = $CargoTarget
$env:TEMP = Join-Path $OutputRoot 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
Push-Location $desktop
try {
    & .\node_modules\.bin\tsc.cmd -b --pretty false
    if ($LASTEXITCODE -ne 0) { throw 'Typecheck failed' }
    & .\node_modules\.bin\vite.cmd build --outDir $webOutput
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
Push-Location $native
try {
    # Tauri's untagged FrontendDist attempts URL first. E:/... is a URL, not a directory.
    # Resolve the explicit E: output to a relative directory so assets are embedded.
    $relativeWeb = (Resolve-Path -LiteralPath $webOutput -Relative).Replace('\','/')
    if ($relativeWeb -match '^[a-zA-Z]+:') { throw 'Frontend directory must not parse as a URL' }
    $env:TAURI_CONFIG = @{build=@{frontendDist=$relativeWeb}} | ConvertTo-Json -Compress
    & cargo build --release --offline --features tauri/custom-protocol --manifest-path (Join-Path $native 'Cargo.toml') --target-dir $CargoTarget
    if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
    Copy-Item -LiteralPath (Join-Path $CargoTarget 'release\content-factory-desktop.exe') -Destination (Join-Path $OutputRoot 'content-factory-desktop.exe')
} finally { Pop-Location }
Write-Host "Build ready: $OutputRoot. No installed app or user data was replaced."
