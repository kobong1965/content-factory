[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$PythonArchive,
    [Parameter(Mandatory=$true)][string]$PythonArchiveSha256,
    [Parameter(Mandatory=$true)][string]$ApiSitePackages,
    [Parameter(Mandatory=$true)][string]$CryptoSitePackages,
    [Parameter(Mandatory=$true)][string]$AsrSitePackages,
    [Parameter(Mandatory=$true)][string]$SpeechModel,
    [Parameter(Mandatory=$true)][string]$WhisperModel,
    [Parameter(Mandatory=$true)][string]$FFmpegRoot
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output=[IO.Path]::GetFullPath($OutputRoot)
if (!$output.StartsWith('E:\Codex工作盘\artifacts\test-builds\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unverified runtime staging must stay under artifacts/test-builds'
}
if (Test-Path -LiteralPath $output) { throw 'Refusing to overwrite an existing stage' }
foreach($required in @($PythonArchive,$ApiSitePackages,$CryptoSitePackages,$AsrSitePackages,$SpeechModel,$WhisperModel,$FFmpegRoot)) {
    if (!(Test-Path -LiteralPath $required)) { throw "Required packaging input missing: $required" }
}
if ((Get-FileHash -LiteralPath $PythonArchive -Algorithm SHA256).Hash -ne $PythonArchiveSha256) { throw 'Python archive checksum mismatch' }
New-Item -ItemType Directory -Path $output | Out-Null
function Copy-Tree([string]$Source,[string]$Target) {
    & robocopy.exe $Source $Target /E /XD __pycache__ .cache .pytest_cache node_modules tests test pip pytest _pytest /XF '*.pyc' '*.log' '__editable__*' 'direct_url.json' /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Copy failed: $Source" }
}
foreach($role in @('python','asr-python')) {
    $destination=Join-Path $output "runtime\$role"
    if($PythonArchive.EndsWith('.tar.gz',[StringComparison]::OrdinalIgnoreCase)) {
        New-Item -ItemType Directory -Path $destination | Out-Null
        & tar.exe -xzf $PythonArchive --strip-components=1 -C $destination
        if($LASTEXITCODE -ne 0) { throw 'Python archive extraction failed' }
    } else {
        Expand-Archive -LiteralPath $PythonArchive -DestinationPath $destination
    }
    $paths=@('python312.zip','.','Lib','DLLs','Lib/site-packages')
    if ($role -eq 'python') {
        $paths+=@('../../services/api/src','../../workers/media/src','../../packages/contracts/python')
    }
    $paths+='import site'
    [IO.File]::WriteAllLines((Join-Path $destination 'python312._pth'),$paths,[Text.UTF8Encoding]::new($false))
}
Copy-Tree $ApiSitePackages (Join-Path $output 'runtime\python\Lib\site-packages')
Copy-Tree $CryptoSitePackages (Join-Path $output 'runtime\python\Lib\site-packages')
Copy-Tree $AsrSitePackages (Join-Path $output 'runtime\asr-python\Lib\site-packages')
foreach($part in @('services\api\src','workers\media\src','packages\contracts\python','packages\contracts\schemas')) {
    Copy-Tree (Join-Path $repo $part) (Join-Path $output $part)
}
New-Item -ItemType Directory -Path (Join-Path $output 'scripts'),(Join-Path $output 'tools'),(Join-Path $output 'models\whisper'),(Join-Path $output 'licenses\ffmpeg') | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'scripts\speech-caption-worker.py') -Destination (Join-Path $output 'scripts')
Copy-Item -LiteralPath (Join-Path $repo 'package.json') -Destination $output
Copy-Item -LiteralPath $WhisperModel -Destination (Join-Path $output 'models\whisper\ggml-tiny.bin')
Copy-Tree $SpeechModel (Join-Path $output 'models\large-v3-turbo')
foreach($name in @('ffmpeg.exe','ffprobe.exe')) {
    Copy-Item -LiteralPath (Join-Path $FFmpegRoot "bin\$name") -Destination (Join-Path $output 'tools')
}
foreach($name in @('LICENSE','README.txt')) {
    Copy-Item -LiteralPath (Join-Path $FFmpegRoot $name) -Destination (Join-Path $output 'licenses\ffmpeg')
}
$manifest=@{
    schema_version=1
    kind='runtime-staging-only'
    installer_ready=$false
    clean_windows_verified=$false
    python_archive_sha256=$PythonArchiveSha256
    source_version=(Get-Content (Join-Path $repo 'package.json') -Raw | ConvertFrom-Json).version
    excludes=@('user-data','model-credentials','third-party-skill-with-unconfirmed-redistribution-license','desktop-installer','updater')
}
[IO.File]::WriteAllText((Join-Path $output 'staging-manifest.json'),($manifest | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Output "Runtime staged for verification only: $output"
