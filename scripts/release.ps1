# FM-1 Switch release on Windows, as the Noisebud apps go (Pre Flight's release.ps1):
# versions -> simulated tests -> Windows installer (scripts\build-installer.ps1) to the setup folder ->
# Mac source export (tools\export_mac.py: the committed tree, portable ZIP) under exports\, checked by
# Noisebud Builder's delivery gate with this project's custom flow (builder-flows.json) -> the ZIP and
# Build_FM1Switch_Mac.command to SEM. Every copy is made with cmd copy and checked (size, SHA-256).
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1 [-SkipWindowsInstaller] [-SkipMacHandoff]
# Noisebud Builder runs this same script on Windows. One build at a time (build\release.lock), Python 3.12
# unless -Python names another, and only the branch's newest commit. Building the same version again
# (after a failed run) replaces its setup file and export; a change to the app gets a new VERSION.
# On the Mac: register builder-flows.json in Noisebud Builder once (merge it into its flows.json), then
# build "FM-1 Switch · free app" there, or double-click Build_FM1Switch_Mac.command in SEM.
param(
    # (SetupDest first: a caller that passes the setup folder without a name means it, never -Python)
    [Parameter(Position = 0)][string]$SetupDest = 'D:\Dropbox\SynthEdit\000 Klara pluggar 000\--- Nya Setupfiler',
    [switch]$SkipWindowsInstaller,
    [switch]$SkipMacHandoff,
    [string]$Python = '',
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
# Looked for in turn: the py launcher, Python's usual install folders, every python on PATH (Builder's
# environment may lack the launcher). Each candidate is asked for its own version.
function Find-Python312 {
    $tried = @()
    # (no quotes inside: Windows PowerShell 5.1 drops them on the way to a native program)
    $ask = 'import sys; print(sys.executable if sys.version_info[:2] == (3, 12) else str())'
    $candidates = @()
    foreach ($c in @(Get-Command py, py.exe -All -ErrorAction SilentlyContinue)) { $candidates += , @($c.Source, '-3.12') }
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:ProgramFiles\Python312",
                     "${env:ProgramFiles(x86)}\Python312", 'C:\Python312')) {
        $candidates += , @((Join-Path $d 'python.exe'))
    }
    foreach ($c in @(Get-Command python, python3, python3.12 -All -ErrorAction SilentlyContinue)) { $candidates += , @($c.Source) }
    foreach ($c in $candidates) {
        if (-not $c[0] -or -not (Test-Path -LiteralPath $c[0])) { continue }
        $tried += ($c -join ' ')
        $was = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'      # (a native program's error output would stop the script)
        $more = @($c | Select-Object -Skip 1)
        # (all of its output, then the first line: Select-Object -First 1 would stop it early and leave a
        # false exit code behind, which is what made 1.5.12's first try say 3.12 was missing)
        try { $out = @(& $c[0] $more -c $ask 2>$null) } catch { $out = @() }
        $ErrorActionPreference = $was
        $first = if ($out.Count) { ([string]$out[0]).Trim() } else { '' }
        if ($first -and (Test-Path -LiteralPath $first)) { return $first }
    }
    throw ('Python 3.12 was not found (install it from python.org, or pass -Python). Tried: ' +
           $(if ($tried) { $tried -join '; ' } else { 'nothing: no py launcher, no python on PATH' }))
}
if (-not $Python) { $Python = Find-Python312 }
Write-Host "Python: $Python"

# Resilio: PowerShell Copy-Item is unreliable, so cmd copy and verify.
function Copy-Verified([string]$source, [string]$destination, [switch]$Keep) {
    if ($Keep -and (Test-Path -LiteralPath $destination)) {
        # a rebuild of the same version (say after a failed run) replaces it; a change to the app gets a new VERSION
        Write-Host "  $(Split-Path $destination -Leaf): already there, replaced by this build of the same version"
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

$made = @()
if (-not $SkipWindowsInstaller) { $made += 'Windows installer' }
if (-not $SkipMacHandoff) { $made += 'static Mac export' }
Write-Host ("RELEASE OK $version (" + $(if ($made) { $made -join ' and ' } else { 'checks and tests only' }) +
            '; the Mac build itself runs on the Mac)')
