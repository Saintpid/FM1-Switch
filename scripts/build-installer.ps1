# FM-1 Switch's Windows installer, the way the Noisebud apps are built (Pre Flight's build-installer.ps1):
# PyInstaller stages the app (build\win\dist\FM1-Switch), Inno Setup 6 wraps it with installer\EULA.txt
# (free software, no warranty, no liability for any hardware). Signs the app and the installer when
# NOISEBUD_WINDOWS_CERT_THUMBPRINT is set (the same variable as the other Noisebud builds).
#   powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
# Output: build\win\FM-1 Switch Setup <version>.exe (release.ps1 copies it to the setup folder).
param(
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION is not X.Y.Z: $version" }
$work = Join-Path $root 'build\win'
$venv = Join-Path $work 'venv'
$eula = Join-Path $root 'installer\EULA.txt'
$icon = Join-Path $root 'installer\FM1Switch.ico'
$script = Join-Path $root 'installer\FM1Switch.iss'
$baseName = "FM-1 Switch Setup $version"
# Exact versions, as the Mac build pins them.
$packages = @('mido==1.3.3', 'packaging==24.2', 'python-rtmidi==1.5.8', 'pyserial==3.5', 'sv-ttk==2.6.1', 'pyinstaller==6.22.3')

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

foreach ($f in @($eula, $icon, $script)) { if (-not (Test-Path -LiteralPath $f)) { throw "Missing: $f" } }
$compiler = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw 'Inno Setup 6 was not found (https://jrsoftware.org/isdl.php).' }

Write-Host "FM-1 Switch ${version}: Windows installer"
if (Test-Path -LiteralPath $work) { Remove-Item -Recurse -Force -LiteralPath $work }
New-Item -ItemType Directory -Force $work | Out-Null
Invoke-Checked 'venv' { & $Python -m venv $venv }
$py = Join-Path $venv 'Scripts\python.exe'
Invoke-Checked 'pip install' { & $py -m pip install --quiet --disable-pip-version-check @packages }

# The app: one folder (an installer needs no self-extracting .exe), windowed, its icon and documents.
Push-Location (Join-Path $root 'tools')
try {
    Invoke-Checked 'PyInstaller' {
        & $py -m PyInstaller --noconfirm --windowed --name FM1-Switch --icon $icon --paths . `
            --hidden-import mido.backends.rtmidi --hidden-import serial.tools.list_ports --collect-data sv_ttk `
            --add-data "..\VERSION;." --add-data "..\LICENSE;." --add-data "..\DISCLAIMER.md;." `
            --distpath (Join-Path $work 'dist') --workpath (Join-Path $work 'pyi') --specpath $work fm1_switch.pyw
    }
} finally { Pop-Location }
$app = Join-Path $work 'dist\FM1-Switch'
$exe = Join-Path $app 'FM1-Switch.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "PyInstaller made no app: $exe" }

# The built app starts its rescue part (it reports the missing package and exits, writes nothing).
$probe = Join-Path $work 'probe.jsonl'
$p = Start-Process -FilePath $exe -ArgumentList @('--rescue', 'nothing.fwsc', '--progress', "`"$probe`"") -Wait -PassThru -WindowStyle Hidden
if (-not (Select-String -LiteralPath $probe -Pattern '"end": "fail"' -Quiet)) { throw 'The built app did not start its rescue part.' }

$thumbprint = $env:NOISEBUD_WINDOWS_CERT_THUMBPRINT
$signTool = $null
if ($thumbprint) {
    $timestamp = if ($env:NOISEBUD_WINDOWS_TIMESTAMP_URL) { $env:NOISEBUD_WINDOWS_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }
    $signTool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signTool) { throw 'SignTool was not found in the Windows SDK.' }
    Invoke-Checked "Signing $exe" { & $signTool.FullName sign /sha1 $thumbprint /fd SHA256 /tr $timestamp /td SHA256 $exe }
}

Invoke-Checked 'Inno Setup' {
    & $compiler "/DAppVer=$version" "/DAppSrc=$app" "/DEulaFile=$eula" "/DIconFile=$icon" "/DDocDir=$root" `
        "/DOutDir=$work" "/DOutputBaseFilename=$baseName" $script | Out-Host
}
$setup = Join-Path $work "$baseName.exe"
if (-not (Test-Path -LiteralPath $setup)) { throw "The installer was not created: $setup" }
if ($thumbprint) {
    Invoke-Checked "Signing $setup" { & $signTool.FullName sign /sha1 $thumbprint /fd SHA256 /tr $timestamp /td SHA256 $setup }
} else {
    Write-Host 'NOISEBUD_WINDOWS_CERT_THUMBPRINT is not set: the app and the installer are not signed.'
}
Write-Host "Installer: $setup"
Write-Host ("SHA-256: " + (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash)
