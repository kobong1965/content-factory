[CmdletBinding()]
param([string]$RunRoot = '', [string]$PlaywrightModule = '')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$storage = Join-Path 'E:\' ('Codex' + [char]0x5DE5 + [char]0x4F5C + [char]0x76D8)
if (-not $RunRoot) { $RunRoot = Join-Path $storage ('temp\huashu-acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$RunRoot = [IO.Path]::GetFullPath($RunRoot)
if (-not $RunRoot.StartsWith($storage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Use an E: Codex workspace path.' }
if (Test-Path -LiteralPath $RunRoot) { throw 'Choose a new RunRoot; existing evidence is not overwritten.' }
if (-not $PlaywrightModule) { $PlaywrightModule = Join-Path $storage 'caches\npm-playwright-cli\_npx\31e32ef8478fbf80\node_modules\playwright' }
if (-not (Test-Path -LiteralPath $PlaywrightModule)) { throw 'Provide an existing Playwright module; no dependencies are downloaded.' }
foreach ($port in @(18768,1420)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "QA port $port occupied; no process was stopped." }
}
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$env:TEMP = Join-Path $RunRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $RunRoot 'pycache'
$env:PYTHONIOENCODING = 'utf-8'
$env:VITE_API_URL = 'http://127.0.0.1:18768'
$env:PYTHONPATH = (@('packages\contracts\python','workers\media\src','services\api\src') | ForEach-Object { Join-Path $repo $_ }) -join ';'
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$python = Join-Path $repo '.venv\Scripts\python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$started = [Collections.Generic.List[Diagnostics.Process]]::new()
function Start-HuashuQa([string]$Name,[string]$Executable,[string[]]$Arguments) {
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RunRoot "$Name.stdout.log") -RedirectStandardError (Join-Path $RunRoot "$Name.stderr.log")
    $started.Add($process)
}
function Wait-HuashuHttp([string]$Url) {
    $deadline = (Get-Date).AddSeconds(35)
    while ((Get-Date) -lt $deadline) {
        try { $null = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2; return } catch { Start-Sleep -Milliseconds 250 }
    }
    throw "QA service did not start: $Url. Inspect logs under $RunRoot."
}
try {
    Start-HuashuQa 'api' $python @('-B',(Join-Path $PSScriptRoot 'huashu-qa-server.py'),'--root',$RunRoot,'--port','18768')
    Start-HuashuQa 'vite' $node @((Join-Path $PSScriptRoot 'huashu-qa-vite.mjs'),(Join-Path $repo 'apps\desktop'),$RunRoot)
    Wait-HuashuHttp 'http://127.0.0.1:18768/health'
    Wait-HuashuHttp 'http://127.0.0.1:1420'
    & $node (Join-Path $PSScriptRoot 'verify-huashu-analysis.cjs') --root $RunRoot --playwright-module $PlaywrightModule
    if ($LASTEXITCODE -ne 0) { throw "Huashu browser acceptance failed: $RunRoot" }
    Write-Host "Huashu acceptance passed: $RunRoot"
} finally {
    foreach ($process in $started) { if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force } }
}
