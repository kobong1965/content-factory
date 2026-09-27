[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$NSIS,[Parameter(Mandatory=$true)][string]$BootstrapPython,[Parameter(Mandatory=$true)][string]$DeliveryRoot,[Parameter(Mandatory=$true)][string]$TemporaryRoot)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version=(Get-Content (Join-Path $repo 'package.json') -Raw|ConvertFrom-Json).version
$manifest=Join-Path $DeliveryRoot 'delivery-manifest.json'
$m=Get-Content $manifest -Raw|ConvertFrom-Json
if($m.version -ne $version){throw 'Payload version mismatch'}
$out=Join-Path $DeliveryRoot "content-factory-$version-single-setup.exe"
$internal='E:\Codex工作盘\artifacts\latest\爆款内容工厂-内部Skill-0.1.31'
$env:TEMP=$TemporaryRoot;$env:TMP=$TemporaryRoot
New-Item -ItemType Directory -Force -Path $TemporaryRoot|Out-Null
& $NSIS /INPUTCHARSET UTF8 /V2 "/DVERSION=$version" "/DOUTPUT=$out" "/DPYTHON=$BootstrapPython" "/DWORKER=$repo\scripts\install_payload.py" "/DMANIFEST=$manifest" "/DPROGRAM=$(Join-Path $DeliveryRoot $m.parts[0].name)" "/DSKILLS=$internal\business-skills.cfskills" "/DHUASHU=$internal\huashu-analysis.SKILL.md" (Join-Path $PSScriptRoot 'windows-installer-single.nsi')
if($LASTEXITCODE -ne 0){throw 'Single-file installer build failed'}
Write-Output $out
