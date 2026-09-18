$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$fixtureDirectory = Join-Path $projectRoot "output\s2\fixture"
$fixturePath = Join-Path $fixtureDirectory "s2-fixture.mp4"

New-Item -ItemType Directory -Force -Path $fixtureDirectory | Out-Null

$ffmpegArguments = @(
    "-y", "-v", "error",
    "-f", "lavfi", "-i", "color=c=0x111827:s=360x640:r=25:d=2",
    "-f", "lavfi", "-i", "color=c=0x0f766e:s=360x640:r=25:d=2",
    "-f", "lavfi", "-i", "color=c=0xbe123c:s=360x640:r=25:d=2",
    "-f", "lavfi", "-i", "flite=text='This is the S two media pipeline acceptance fixture.':voice=slt",
    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]apad=pad_dur=6[a]",
    "-map", "[v]", "-map", "[a]", "-t", "6",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
    $fixturePath
)

& ffmpeg @ffmpegArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $fixturePath)) {
    throw "Failed to create the S2 acceptance fixture."
}

Write-Host "S2 fixture is ready: $fixturePath"
