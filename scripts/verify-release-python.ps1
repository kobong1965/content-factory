[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonExecutable,
    [Parameter(Mandatory=$true)][string]$RunRoot,
    [string[]]$AdditionalPythonPath=@()
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$run=[IO.Path]::GetFullPath($RunRoot)
if (!$run.StartsWith('E:\Codex工作盘\',[StringComparison]::OrdinalIgnoreCase)) { throw 'RunRoot must stay on the work drive' }
if (Test-Path -LiteralPath $run) { throw 'Use a fresh test directory' }
New-Item -ItemType Directory -Path (Join-Path $run 'temp') | Out-Null
$env:TEMP=Join-Path $run 'temp'
$env:TMP=$env:TEMP
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$paths=@('packages\contracts\python','workers\media\src','services\api\src') | ForEach-Object { Join-Path $repo $_ }
$env:PYTHONPATH=(@($paths)+$AdditionalPythonPath) -join ';'
foreach($stage in @('runtime','media','analysis')) {
    [Environment]::SetEnvironmentVariable('CONTENT_FACTORY_'+$stage.ToUpper()+'_ROOT',(Join-Path $run $stage))
}
foreach($stage in @('s4','s5','s6','s7','s8')) {
    [Environment]::SetEnvironmentVariable('CONTENT_FACTORY_'+$stage.ToUpper()+'_DATA_DIR',(Join-Path $run $stage))
}
$env:CONTENT_FACTORY_S3_CONFIG_PATH=Join-Path $run 'gateway\config.json'
$env:CONTENT_FACTORY_MEDIA_TEMP_ROOT=Join-Path $run 'media-logs'
foreach($stage in @('S2','S3','S5','S6','S7','S8')) {
    [Environment]::SetEnvironmentVariable('CONTENT_FACTORY_'+$stage+'_INTEGRATION','1')
}
Push-Location $repo
try {
    & $PythonExecutable -B -m pytest packages/contracts/tests workers/media/tests services/api/tests --basetemp (Join-Path $run 'pytest') -p no:cacheprovider --junitxml (Join-Path $run 'results.xml') -q
    if($LASTEXITCODE -ne 0){ throw 'Release Python regression failed; inspect results.xml' }
} finally { Pop-Location }
