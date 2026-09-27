[CmdletBinding()]
param(
    [string]$RunRoot = 'E:\Codex工作盘\temp\workflow032-browser-06-evidence',
    [string]$BuildRoot = 'E:\Codex工作盘\artifacts\test-builds\content-factory-0.1.32-workflow-final',
    [string]$PythonExecutable = 'E:\Codex项目盘\男装编剪器\.venv\Scripts\python.exe',
    [int]$Port = 18768
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RunRoot = [IO.Path]::GetFullPath($RunRoot)
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
if (-not $RunRoot.StartsWith('E:\Codex工作盘\temp\', [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath (Join-Path $RunRoot 'evidence\results.json'))) {
    throw 'Only a completed, isolated workflow QA directory may be previewed.'
}
if (-not $BuildRoot.StartsWith('E:\Codex工作盘\artifacts\test-builds\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview must use a test build, not the installed application.'
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied; no existing service was stopped."
}
$previewRoot = Join-Path $RunRoot 'native-preview'
New-Item -ItemType Directory -Force -Path $previewRoot | Out-Null
$env:TEMP = Join-Path $previewRoot 'temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $previewRoot 'webview'
$env:CONTENT_FACTORY_API_PORT = "$Port"
$env:CONTENT_FACTORY_USER_ROOT = $previewRoot
$env:CONTENT_FACTORY_RUNTIME_ROOT = Join-Path $RunRoot 'runtime'
$env:CONTENT_FACTORY_MEDIA_ROOT = Join-Path $RunRoot 'media'
$env:CONTENT_FACTORY_MEDIA_TEMP_ROOT = Join-Path $RunRoot 'media-logs'
$env:CONTENT_FACTORY_ANALYSIS_ROOT = Join-Path $RunRoot 'analysis'
$env:CONTENT_FACTORY_S3_CONFIG_PATH = Join-Path $RunRoot 'gateway.json'
$env:CONTENT_FACTORY_EXPORT_ROOT = Join-Path $RunRoot 'downloads'
$env:PYTHONPYCACHEPREFIX = Join-Path $previewRoot 'pycache'
$env:PYTHONPATH = (@('packages\contracts\python', 'workers\media\src', 'services\api\src') | ForEach-Object { Join-Path $repo $_ }) -join ';'
$env:PYTHONPATH += ';E:\Codex工作盘\runtimes\content-factory-release-crypto'
foreach ($stage in 4..8) { [Environment]::SetEnvironmentVariable("CONTENT_FACTORY_S${stage}_DATA_DIR", (Join-Path $RunRoot "s$stage"), 'Process') }
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$apiProcess = $null
try {
    $apiProcess = Start-Process -FilePath $PythonExecutable -ArgumentList @('-B','-m','uvicorn','content_factory_api.main:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $previewRoot 'api.stdout.log') -RedirectStandardError (Join-Path $previewRoot 'api.stderr.log')
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($apiProcess.HasExited) { throw 'Isolated API exited. See native-preview/api.stderr.log.' }
        try { $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1; $ready = $true; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready) { throw 'Isolated API startup timed out.' }
    Write-Host 'Opening TEST preview. All records are synthetic. Close its window to stop the preview service.'
    # This is the interactive test window, not a background helper.
    $desktopProcess = Start-Process -FilePath (Join-Path $BuildRoot 'content-factory-desktop.exe') -WorkingDirectory $BuildRoot -PassThru
    $desktopProcess.WaitForExit()
} finally {
    if ($null -ne $apiProcess -and -not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id }
}
