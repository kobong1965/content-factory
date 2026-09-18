[CmdletBinding()]
param([string]$RunRoot = '')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$storage = Join-Path 'E:\' ('Codex' + [char]0x5DE5 + [char]0x4F5C + [char]0x76D8)
if (-not $RunRoot) { $RunRoot = Join-Path $storage ('temp\relay-stream-acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$RunRoot = [IO.Path]::GetFullPath($RunRoot)
if (-not $RunRoot.StartsWith($storage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'RunRoot must stay under E: Codex workspace storage.' }
if (Test-Path -LiteralPath $RunRoot) { throw 'Use a new RunRoot; existing evidence is preserved.' }
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$env:TEMP = Join-Path $RunRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $RunRoot 'pycache'
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$env:CONTENT_FACTORY_RUNTIME_ROOT = Join-Path $RunRoot 'runtime'
$env:CONTENT_FACTORY_MEDIA_ROOT = Join-Path $RunRoot 'media'
$env:CONTENT_FACTORY_ANALYSIS_ROOT = Join-Path $RunRoot 'analysis'
$env:CONTENT_FACTORY_S3_CONFIG_PATH = Join-Path $RunRoot 'gateway/settings.json'
foreach ($stage in @('S4','S5','S6','S7','S8')) {
    [Environment]::SetEnvironmentVariable("CONTENT_FACTORY_${stage}_DATA_DIR", (Join-Path $RunRoot $stage), 'Process')
}
$env:PYTHONPATH = (@('packages/contracts/python','workers/media/src','services/api/src') | ForEach-Object { Join-Path $repo $_ }) -join ';'
$tests = @('streaming_io','transport','protocol','completion') | ForEach-Object { Join-Path $repo "services/api/tests/test_s3_gateway_$_.py" }
$tests += @('observability','segmented_analysis','analysis_method','resilient_queue') | ForEach-Object { Join-Path $repo "services/api/tests/test_s3_$_.py" }
& (Join-Path $repo '.venv/Scripts/python.exe') -m pytest @tests --basetemp (Join-Path $RunRoot 'pytest') -o "cache_dir=$RunRoot/pytest-cache" -q 2>&1 | Tee-Object -FilePath (Join-Path $RunRoot 'tests.log')
if ($LASTEXITCODE -ne 0) { throw "Relay acceptance failed. Evidence: $RunRoot" }
Write-Host "Relay acceptance passed using local simulated HTTP servers. No paid cloud calls. Evidence: $RunRoot"
