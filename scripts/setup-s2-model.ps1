$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$modelDirectory = Join-Path $projectRoot ".models\whisper"
$modelPath = Join-Path $modelDirectory "ggml-tiny.bin"
$temporaryPath = Join-Path $modelDirectory "ggml-tiny.download"
$expectedSha1 = "bd577a113a864445d4c299885e0cb97d4ba92b5f"
$downloadUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin?download=true"

function Get-Sha1 {
    param([Parameter(Mandatory = $true)] [string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha1 = [System.Security.Cryptography.SHA1]::Create()
        try {
            return ([System.BitConverter]::ToString($sha1.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $sha1.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null

if (Test-Path -LiteralPath $modelPath) {
    $currentSha1 = Get-Sha1 -Path $modelPath
    if ($currentSha1 -eq $expectedSha1) {
        Write-Host "S2 ASR model is ready: $modelPath"
        exit 0
    }
}

Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
Write-Host "Downloading the S2 Whisper tiny model (about 75 MB)..."
Invoke-WebRequest -Uri $downloadUrl -OutFile $temporaryPath

$downloadedSha1 = Get-Sha1 -Path $temporaryPath
if ($downloadedSha1 -ne $expectedSha1) {
    Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    throw "Whisper model checksum mismatch. Expected $expectedSha1 but received $downloadedSha1."
}

Move-Item -LiteralPath $temporaryPath -Destination $modelPath -Force
Write-Host "S2 ASR model downloaded and verified: $modelPath"
