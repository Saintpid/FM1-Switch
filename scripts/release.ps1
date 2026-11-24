# FM-1 Switch release on Windows, as the Noisebud apps go (Pre Flight's release.ps1):
# versions -> simulated tests -> Windows installer (scripts\build-installer.ps1) to the setup folder ->
# Mac source export (tools\export_mac.py: the committed tree, portable ZIP) under exports\, checked by
# Noisebud Builder's delivery gate with this project's custom flow (builder-flows.json) -> the ZIP and
# Build_FM1Switch_Mac.command to SEM. Every copy is made with cmd copy and checked (size, SHA-256).
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1 [-SkipWindowsInstaller] [-SkipMacHandoff]
# Noisebud Builder runs this same script on Windows. One build at a time (build\release.lock), Python 3.12
# unless -Python names another, only the branch's newest commit, and no setup file or export replaced
# by a different one of the same name.
# On the Mac: register builder-flows.json in Noisebud Builder once (merge it into its flows.json), then
# build "FM-1 Switch · free app" there, or double-click Build_FM1Switch_Mac.command in SEM.
param(
    [switch]$SkipWindowsInstaller,
    [switch]$SkipMacHandoff,
    [string]$Python = '',
    [string]$SetupDest = 'D:\Dropbox\SynthEdit\000 Klara pluggar 000\--- Nya Setupfiler',
    [string]$SemDir = 'C:\Users\Johannes\Documents\SynthEdit Projects\Mac Export\SEM',
    [string]$CheckExport = 'C:\Projects\Noisebud Builder\tools\check_export.py'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$macCommand = 'Build_FM1Switch_Mac.command'

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

# One build at a time: a second one (Builder, or a hand run) stops here instead of deleting the first's files.
# Windows lets go of the lock when this process ends, however it ends.
New-Item -ItemType Directory -Force (Join-Path $root 'build') | Out-Null
try {
    $lock = [System.IO.File]::Open((Join-Path $root 'build\release.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    throw 'Another FM-1 Switch build is running in this folder (Noisebud Builder or release.ps1). Let it finish, then build again.'
}

# The same Python every time, so one version is one installer: 3.12 unless -Python says otherwise.
if (-not $Python) {
    # (through cmd: Windows PowerShell stops on a native program's error output, even redirected)
    $Python = (cmd /c 'py -3.12 -c "import sys; print(sys.executable)" 2>nul' | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'Python 3.12 was not found (py -3.12). Install it, or pass -Python.' }
    $Python = $Python.Trim()
}
Write-Host "Python: $Python"

# Resilio: PowerShell Copy-Item is unreliable, so cmd copy and verify.
function Copy-Verified([string]$source, [string]$destination, [switch]$Keep) {
    if ($Keep -and (Test-Path -LiteralPath $destination)) {
        # never replace a setup file or export: the same bytes are fine, different ones stop the release
        $had = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($had -ne (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash) {
            throw "$destination already exists and is a different build. Bump VERSION for a new build."
        }
        Write-Host "  $(Split-Path $destination -Leaf): already there, the same bytes"
        return
    }
    cmd /c copy /Y "$source" "$destination" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Copy failed: $source" }
    $src = Get-Item -LiteralPath $source
    $dst = Get-Item -LiteralPath $destination
    $srcHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($src.Length -ne $dst.Length -or $srcHash -ne $dstHash) { throw "Copy verification failed: $destination" }
    Write-Host "  $($dst.Name): $($dst.Length) bytes, SHA-256 $dstHash"
}

# 1. The version and its changelog section.
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION is not X.Y.Z: $version" }
if (-not (Select-String -LiteralPath (Join-Path $root 'CHANGELOG.md') -Pattern ("^## " + [regex]::Escape($version) + "$") -Quiet)) {
    throw "CHANGELOG.md has no '## $version' section"
}
Write-Host "FM-1 Switch $version"

Push-Location $root
try {
    # 2. The Mac export is the committed tree, so it must be clean.
    if (git status --porcelain --untracked-files=no) { throw 'Uncommitted changes: commit before releasing.' }
    # Only the branch's newest commit (GitHub's), so an old checkout is not built by mistake.
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -eq 'HEAD') { throw 'This folder is not on a branch: check out the branch to build.' }
    cmd /c "git fetch --quiet origin $branch 2>nul"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  (could not reach GitHub: building $branch as it is here, $(git rev-parse --short HEAD))"
    } elseif ((git rev-parse HEAD).Trim() -ne (git rev-parse FETCH_HEAD).Trim()) {
        throw "This folder is not on the newest $branch from GitHub: run git pull --ff-only first."
    }

    # 3. Simulated tests (no hardware).
    foreach ($t in @('install', 'switch', 'rescue', 'export')) {
        Invoke-Checked "$t test" { & $Python "tests\${t}_test.py" | Out-Host }
    }

    # 4. Windows installer to the setup folder.
    if (-not $SkipWindowsInstaller) {
        Invoke-Checked 'Windows installer' { powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\build-installer.ps1" -Python $Python }
        $setup = Join-Path $root "build\win-$version\FM-1 Switch Setup $version.exe"
        if (-not (Test-Path -LiteralPath $setup)) { throw "Installer not found: $setup" }
        Write-Host "Copying the installer to $SetupDest"
        Copy-Verified $setup (Join-Path $SetupDest (Split-Path $setup -Leaf)) -Keep
    }

    # 5. Mac handoff: the shared signing/native runtime as upstream has it, export, Builder's gate, SEM.
    if (-not $SkipMacHandoff) {
        $pins = Get-Content (Join-Path $root 'mac\SOURCE_HASHES.json') -Raw | ConvertFrom-Json
        foreach ($entry in $pins.files.PSObject.Properties) {
            $local = Join-Path $root $entry.Name
            $upstream = ($entry.Value.source -split ' \(')[0]
            $localHash = (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLower()
            if ($localHash -ne $entry.Value.sha256) { throw "$($entry.Name) differs from its pinned hash" }
            if (Test-Path -LiteralPath $upstream) {
                # Text files are compared without CR: upstream's working copy may be CRLF, ours is LF.
                $upBytes = [System.IO.File]::ReadAllBytes($upstream)
                if ($upstream -match '\.(py|json)$') {
                    $upBytes = [byte[]]($upBytes | Where-Object { $_ -ne 13 })
                    $ourBytes = [byte[]]([System.IO.File]::ReadAllBytes($local) | Where-Object { $_ -ne 13 })
                    $sha = [System.Security.Cryptography.SHA256]::Create()
                    $upHash = (($sha.ComputeHash($upBytes) | ForEach-Object { $_.ToString('x2') }) -join '')
                    $cmpHash = (($sha.ComputeHash($ourBytes) | ForEach-Object { $_.ToString('x2') }) -join '')
                } else {
                    $upHash = (Get-FileHash -LiteralPath $upstream -Algorithm SHA256).Hash.ToLower()
                    $cmpHash = $localHash
                }
                if ($upHash -ne $cmpHash) { throw "$($entry.Name) is not upstream's current ${upstream}; import it and update mac\SOURCE_HASHES.json" }
            } else {
                Write-Host "  (upstream not found here, pinned hash only: ${upstream})"
            }
        }
        Write-Host 'Shared Mac runtime copies match upstream.'
        Invoke-Checked 'Mac export' { & $Python "$root\tools\export_mac.py" --out "$root\exports" | Out-Host }
        $zipName = "FM1Switch_mac_ready_v$version.zip"
        $zip = Join-Path $root "exports\$zipName"
        if (-not (Test-Path -LiteralPath $CheckExport)) { throw "Noisebud Builder's check_export.py was not found: $CheckExport" }
        $report = & $Python $CheckExport $zip --flows "$root\builder-flows.json"
        $code = $LASTEXITCODE
        $report | Out-Host
        if ($code -ne 0 -or -not ($report -match 'EXPORT OK')) { throw 'check_export.py did not report EXPORT OK' }
        ($report -join "`n") | Set-Content -LiteralPath (Join-Path $root "exports\$zipName.check.json") -Encoding UTF8
        Write-Host "Copying the Mac export to $SemDir"
        Copy-Verified $zip (Join-Path $SemDir $zipName) -Keep
        Copy-Verified (Join-Path $root "tools\$macCommand") (Join-Path $SemDir $macCommand)
        Copy-Verified (Join-Path $root 'builder-flows.json') (Join-Path $SemDir 'FM1Switch_builder-flows.json')
        $newest = Get-ChildItem -LiteralPath $SemDir -Filter 'FM1Switch_mac_ready_v*.zip' |
            Where-Object { $_.BaseName -match '_v\d+\.\d+\.\d+$' } |
            Sort-Object { [version]($_.BaseName -replace '^.*_v', '') } | Select-Object -Last 1
        if ($newest.Name -ne $zipName) { throw "SEM has a newer FM-1 Switch export than this release: $($newest.Name)" }
        Write-Host "  $zipName is the newest FM-1 Switch export in SEM."
        Write-Host 'Builder flow (register once on the Mac, merged into its flows.json): builder-flows.json'
    }
} finally { Pop-Location }

Write-Host "RELEASE OK $version (Windows installer and static Mac export; the Mac build itself runs on the Mac)"
