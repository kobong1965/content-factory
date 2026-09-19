[CmdletBinding()]
param([string]$RunRoot = '', [string]$PlaywrightModule = '', [string]$BrowserScript = 'verify-s5-dialog.cjs', [string]$PythonExecutable = '', [string[]]$AdditionalPythonPath = @())
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$storage = Join-Path 'E:\' ('Codex' + [char]0x5DE5 + [char]0x4F5C + [char]0x76D8)
if (-not $RunRoot) { $RunRoot = Join-Path $storage ('temp\content-factory-s5-dialog-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$RunRoot = [IO.Path]::GetFullPath($RunRoot)
if (-not $RunRoot.StartsWith($storage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'RunRoot must be under E: Codex workspace storage.' }
if (Test-Path -LiteralPath $RunRoot) { throw 'Choose a new RunRoot; existing test data is not overwritten.' }
if (-not $PlaywrightModule) { $PlaywrightModule = Join-Path $storage 'caches\npm-playwright-cli\_npx\31e32ef8478fbf80\node_modules\playwright' }
if (-not (Test-Path -LiteralPath $PlaywrightModule)) { throw 'Provide -PlaywrightModule with an existing Playwright package; this script does not download dependencies.' }
$python = Join-Path $repo '.venv\Scripts\python.exe'
if ($PythonExecutable) { $python = $PythonExecutable }
$node = (Get-Command node -ErrorAction Stop).Source
$desktop = Join-Path $repo 'apps\desktop'
foreach ($port in @(18767, 18411, 1420)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "QA port $port is occupied; no existing process was stopped." }
}
New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null
$env:TEMP = Join-Path $RunRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $RunRoot 'pycache'
$env:CONTENT_FACTORY_RUNTIME_ROOT = Join-Path $RunRoot 'runtime'
$env:CONTENT_FACTORY_MEDIA_ROOT = Join-Path $RunRoot 'media'
$env:CONTENT_FACTORY_MEDIA_TEMP_ROOT = Join-Path $RunRoot 'media-logs'
$env:CONTENT_FACTORY_ANALYSIS_ROOT = Join-Path $RunRoot 'analysis'
$env:CONTENT_FACTORY_S3_CONFIG_PATH = Join-Path $RunRoot 'gateway.json'
$env:CONTENT_FACTORY_EXPORT_ROOT = Join-Path $RunRoot 'downloads'
foreach ($stage in 4..8) { [Environment]::SetEnvironmentVariable("CONTENT_FACTORY_S${stage}_DATA_DIR", (Join-Path $RunRoot "s$stage"), 'Process') }
$env:PYTHONPATH = (@('packages\contracts\python', 'workers\media\src', 'services\api\src') | ForEach-Object { Join-Path $repo $_ }) -join ';'
if ($AdditionalPythonPath.Count) { $env:PYTHONPATH += ';' + ($AdditionalPythonPath -join ';') }
$env:VITE_API_URL = 'http://127.0.0.1:18767'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $python -B (Join-Path $PSScriptRoot 'seed-s5-qa.py') --root $RunRoot --relay-port 18411
if ($LASTEXITCODE -ne 0) { throw 'QA fixture seed failed.' }

# Vite runs from the existing source; all cache and generated configuration stay in RunRoot.
$viteModule = ([Uri](Join-Path $desktop 'node_modules\vite\dist\node\index.js')).AbsoluteUri | ConvertTo-Json -Compress
$desktopJson = $desktop | ConvertTo-Json -Compress
$cacheJson = (Join-Path $RunRoot 'vite-cache') | ConvertTo-Json -Compress
$viteScript = Join-Path $RunRoot 'vite-server.mjs'
@"
import { createServer } from $viteModule;
const server = await createServer({ root: $desktopJson, cacheDir: $cacheJson, configLoader: 'runner', server: { host: '127.0.0.1', port: 1420, strictPort: true } });
await server.listen();
"@ | Set-Content -LiteralPath $viteScript -Encoding UTF8

$started = [Collections.Generic.List[Diagnostics.Process]]::new()
function Start-QaService([string]$Name, [string]$Executable, [string[]]$Arguments) {
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RunRoot "$Name.stdout.log") -RedirectStandardError (Join-Path $RunRoot "$Name.stderr.log")
    $started.Add($process)
}
function Wait-QaHttp([string]$Address) {
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        try { $null = Invoke-WebRequest -Uri $Address -UseBasicParsing -TimeoutSec 2; return } catch { Start-Sleep -Milliseconds 250 }
    }
    throw "QA service did not start: $Address. Inspect logs in $RunRoot"
}
try {
    Start-QaService 'relay' $python @('-B', (Join-Path $PSScriptRoot 's5-mock-relay.py'), '--port', '18411')
    Start-QaService 'api' $python @('-B', '-m', 'uvicorn', 'content_factory_api.main:app', '--host', '127.0.0.1', '--port', '18767')
    Start-QaService 'vite' $node @($viteScript)
    Wait-QaHttp 'http://127.0.0.1:18767/health'
    Wait-QaHttp 'http://127.0.0.1:1420'
    & $node (Join-Path $PSScriptRoot $BrowserScript) --url 'http://127.0.0.1:1420' --api-url 'http://127.0.0.1:18767' --playwright-module $PlaywrightModule --output (Join-Path $RunRoot 'evidence') --generation
    if ($LASTEXITCODE -ne 0) { throw "S5 dialog acceptance failed. Evidence: $RunRoot" }
    Write-Host "S5 dialog acceptance passed. Evidence: $RunRoot"
} finally {
    foreach ($process in $started) { if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force } }
}
