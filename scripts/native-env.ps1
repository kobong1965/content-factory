Set-StrictMode -Version Latest

function Initialize-NativeBuildEnvironment {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path -LiteralPath $cargoBin) {
        $env:PATH = "$cargoBin;$env:PATH"
    }

    $vsWhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vsWhere)) {
        throw "Visual Studio Build Tools discovery utility is missing: $vsWhere"
    }

    $vsInstallPath = & $vsWhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if (-not $vsInstallPath) {
        throw "Visual Studio C++ Build Tools are missing."
    }

    $vsDevCmd = Join-Path $vsInstallPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $vsDevCmd)) {
        throw "Visual Studio developer environment script is missing: $vsDevCmd"
    }

    $developerCommand = "`"$vsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
    $environmentLines = & $env:ComSpec /d /s /c $developerCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to initialize the Visual Studio developer environment."
    }

    foreach ($entry in $environmentLines) {
        if ($entry -match "^(?<name>[^=]+)=(?<value>.*)$") {
            [Environment]::SetEnvironmentVariable($Matches.name, $Matches.value, "Process")
        }
    }
}
