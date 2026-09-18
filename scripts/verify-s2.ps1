$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$fixture = Join-Path $projectRoot "output\s2\fixture\s2-fixture.mp4"
$model = Join-Path $projectRoot ".models\whisper\ggml-tiny.bin"
$apiRoot = Join-Path $projectRoot "output\s2\api-acceptance"
$runtimeRoot = Join-Path $apiRoot "runtime"
$mediaRoot = Join-Path $apiRoot "media"
$responsePath = Join-Path $apiRoot "upload-response.json"
$stdoutPath = Join-Path $apiRoot "api.stdout.log"
$stderrPath = Join-Path $apiRoot "api.stderr.log"
$port = 8876
$baseUrl = "http://127.0.0.1:$port"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)] [string]$Label,
        [Parameter(Mandatory = $true)] [scriptblock]$Command
    )
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Write-Host "S2 verification: $projectRoot" -ForegroundColor Green

$requiredPaths = @(
    "docs\specs\S2-local-media-and-queue.md",
    "packages\contracts\schemas\media-task.schema.json",
    "packages\contracts\schemas\media-result.schema.json",
    "workers\media\src\content_factory_media\pipeline.py",
    "workers\media\src\content_factory_media\queue.py",
    "services\api\src\content_factory_api\s2.py",
    "data\media-validation\manifest.json"
)
foreach ($relativePath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath))) {
        throw "Required S2 path is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Create .venv and install the editable local packages first."
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand "S1 regression" { pnpm verify:s1 }
    Invoke-CheckedCommand "Whisper model checksum" {
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-s2-model.ps1
    }
    Invoke-CheckedCommand "Deterministic media fixture" {
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create-s2-fixture.ps1
    }

    $env:CONTENT_FACTORY_S2_INTEGRATION = "1"
    try {
        Invoke-CheckedCommand "S2 contracts, API, queue and real FFmpeg integration" {
            & $python -m pytest packages\contracts\tests services\api\tests workers\media\tests
        }
    }
    finally {
        Remove-Item Env:CONTENT_FACTORY_S2_INTEGRATION -ErrorAction SilentlyContinue
    }

    Invoke-CheckedCommand "Media task contract fixture" {
        & $python -m content_factory_contracts validate --kind media_task --file packages\contracts\fixtures\media-task.valid.json
    }
    Invoke-CheckedCommand "Media result contract fixture" {
        & $python -m content_factory_contracts validate --kind media_result --file packages\contracts\fixtures\media-result.valid.json
    }
    Invoke-CheckedCommand "Local media capability check" {
        & $python -m content_factory_media s2-check --asr-model $model
    }

    if (Test-Path -LiteralPath $apiRoot) {
        Remove-Item -LiteralPath $apiRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $apiRoot | Out-Null

    $previousRuntimeRoot = $env:CONTENT_FACTORY_RUNTIME_ROOT
    $previousMediaRoot = $env:CONTENT_FACTORY_MEDIA_ROOT
    $previousFixtureMode = $env:CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS
    $env:CONTENT_FACTORY_RUNTIME_ROOT = $runtimeRoot
    $env:CONTENT_FACTORY_MEDIA_ROOT = $mediaRoot
    $env:CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS = "1"
    $server = $null
    try {
        $server = Start-Process -FilePath $python `
            -ArgumentList @("-m", "uvicorn", "content_factory_api.main:app", "--host", "127.0.0.1", "--port", "$port", "--log-level", "warning") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru

        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
                if ($health.status -eq "ok") {
                    $ready = $true
                    break
                }
            }
            catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if (-not $ready) {
            throw "S2 API did not become ready on port $port."
        }

        Invoke-CheckedCommand "Actual HTTP fixture upload" {
            & curl.exe --silent --show-error --fail `
                -H "X-Content-Factory-Fixture: true" `
                -F "video=@$fixture;type=video/mp4" `
                --output $responsePath `
                "$baseUrl/s2/import/file"
        }
        $upload = Get-Content -LiteralPath $responsePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $upload.fixture_data) {
            throw "Acceptance upload lost its fixture_data marker."
        }

        $finished = $null
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            $finished = Invoke-RestMethod -Uri "$baseUrl/s2/tasks/$($upload.task_id)" -TimeoutSec 3
            if ($finished.status -in @("completed", "failed")) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if ($finished.status -ne "completed") {
            throw "Actual HTTP media task did not complete: $($finished.error)"
        }
        $verifiedResultPath = Join-Path $mediaRoot "tasks\$($upload.task_id)\result.json"
        if (-not (Test-Path -LiteralPath $verifiedResultPath)) {
            throw "Actual HTTP media task has no result.json."
        }
        $result = Get-Content -LiteralPath $verifiedResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $result.fixture_data -or $result.asr.status -ne "completed" -or $result.shots.Count -lt 2) {
            throw "Actual HTTP result is incomplete or incorrectly classified."
        }
        $remainingUploads = @(Get-ChildItem -LiteralPath (Join-Path $runtimeRoot "uploads") -File -ErrorAction SilentlyContinue)
        if ($remainingUploads.Count -ne 0) {
            throw "Completed API uploads were not removed from staging storage."
        }
        $readiness = Invoke-RestMethod -Uri "$baseUrl/s2/readiness" -TimeoutSec 5
        if (-not $readiness.engineering_ready -or $readiness.business_ready -or $readiness.accepted_real_videos -ne 0) {
            throw "S2 readiness did not separate engineering from real business acceptance."
        }
        Write-Host "HTTP task completed: $($upload.task_id)" -ForegroundColor Green
    }
    finally {
        if ($null -ne $server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force
            $server.WaitForExit()
        }
        $env:CONTENT_FACTORY_RUNTIME_ROOT = $previousRuntimeRoot
        $env:CONTENT_FACTORY_MEDIA_ROOT = $previousMediaRoot
        $env:CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS = $previousFixtureMode
    }
}
finally {
    Pop-Location
}

Write-Host "`nS2 engineering verification passed. Real business acceptance remains governed by 10 authorized videos." -ForegroundColor Green
