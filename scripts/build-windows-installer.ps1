[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$NSIS,
  [Parameter(Mandatory=$true)][string]$BootstrapPython,
  [Parameter(Mandatory=$true)][string]$DeliveryRoot,
  [Parameter(Mandatory=$true)][string]$TemporaryRoot
)
$ErrorActionPreference='Stop'
foreach($path in @($DeliveryRoot,$TemporaryRoot)) {
  if(![IO.Path]::GetFullPath($path).StartsWith('E:\Codex工作盘\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay on the work drive' }
}
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version=(Get-Content (Join-Path $repo 'package.json') -Raw | ConvertFrom-Json).version
$manifest=Join-Path $DeliveryRoot 'delivery-manifest.json'
if((Get-Content $manifest -Raw | ConvertFrom-Json).version -ne $version) { throw 'Payload version mismatch' }
$env:TEMP=$TemporaryRoot
$env:TMP=$TemporaryRoot
New-Item -ItemType Directory -Force -Path $TemporaryRoot | Out-Null
& $NSIS /INPUTCHARSET UTF8 /V2 "/DVERSION=$version" "/DOUTPUT=$DeliveryRoot\content-factory-$version-setup.exe" "/DPYTHON=$BootstrapPython" "/DWORKER=$repo\scripts\install_payload.py" "/DMANIFEST=$manifest" (Join-Path $PSScriptRoot 'windows-installer.nsi')
if($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
