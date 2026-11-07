param([string]$PreviousSetup)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $root 'dist\ClassroomMate-Setup-win-x64.exe'
$target = Join-Path $env:LOCALAPPDATA 'Programs\ClassroomMate'
$data = Join-Path $env:LOCALAPPDATA 'ClassClaw\ClassroomMate'
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Classroom Mate.lnk'
$entry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassClaw.ClassroomMate_is1'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$app = Join-Path $target 'ClassroomMate.exe'
$startup = '"' + $app + '" --tray'
$upgradeState = 'HKCU:\Software\ClassClaw\ClassroomMate\Setup'

function Get-StartupValue {
    (Get-ItemProperty -LiteralPath $runKey -ErrorAction SilentlyContinue).ClassroomMate
}

function Invoke-SetupProcess($file, $operation, [switch]$ExpectFailure, [string]$ExpectedLog) {
    $log = Join-Path ([IO.Path]::GetTempPath()) "classroom-mate-$operation.log"
    $arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$log`""
    $process = Start-Process -FilePath $file -ArgumentList $arguments -PassThru -Wait
    if (($process.ExitCode -ne 0) -ne $ExpectFailure.IsPresent) {
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
        throw "Unexpected exit code $($process.ExitCode) during $operation."
    }
    if ($ExpectedLog -and !(Select-String -LiteralPath $log -SimpleMatch $ExpectedLog -Quiet)) {
        Get-Content -LiteralPath $log -Tail 80
        throw "Expected upgrade result missing during $operation."
    }
    Write-Host "$operation passed"
}

function Get-InstalledUninstaller {
    $installed = Get-ItemProperty -LiteralPath $entry
    if ($installed.DisplayName -ne 'Classroom Mate' -or $installed.Publisher -ne 'ClassClaw' -or
        $installed.DisplayVersion -ne (Get-Item -LiteralPath $app).VersionInfo.FileVersion -or
        !(Test-Path -LiteralPath $shortcut)) {
        throw 'The Windows uninstall entry or Start menu shortcut is incomplete.'
    }
    $installed.UninstallString.Trim('"')
}

function Assert-Uninstalled {
    foreach ($path in @($target, $shortcut, $entry)) {
        if (Test-Path -LiteralPath $path) { throw "Uninstall left $path behind." }
    }
    if ($null -ne (Get-StartupValue)) { throw 'Uninstall did not remove login startup.' }
}

foreach ($path in @($target, $data, $shortcut, $entry, $upgradeState)) {
    if (Test-Path -LiteralPath $path) { throw "Run the installer test with a clean Windows user: $path already exists." }
}
if ($null -ne (Get-StartupValue)) {
    throw 'A Classroom Mate startup entry already exists.'
}

Invoke-SetupProcess $setup 'install'
$uninstall = Get-InstalledUninstaller
$newVersion = (Get-Item -LiteralPath $app).VersionInfo.FileVersion
$python = Join-Path $target 'runtime\python.exe'
& $python -I -B -c 'import aiortc, av, struct; assert struct.calcsize("P") == 8'
if ($LASTEXITCODE -ne 0) { throw 'The installed x64 media runtime failed to load.' }

# Open the unpaired client on the Windows runner without connecting or opening media devices.
$client = Start-Process -FilePath $app -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        $client.Refresh()
    } until ($client.HasExited -or $client.MainWindowTitle -eq 'Classroom Mate' -or [DateTime]::UtcNow -ge $deadline)
    if ($client.HasExited -or $client.MainWindowTitle -ne 'Classroom Mate') {
        throw "The installed client did not open its dashboard. Window: $($client.MainWindowTitle)"
    }
    Write-Host 'Dashboard opened'
} finally {
    if (!$client.HasExited) {
        Stop-Process -Id $client.Id
        $client.WaitForExit()
    }
}

$fixture = Join-Path $data 'device.dpapi'
Set-Content -LiteralPath $fixture -Value 'simulated device data'
$settings = Join-Path $data 'settings.json'
Set-Content -LiteralPath $settings -Value '{"displayName":"screen","audioOutputName":"speaker"}'
$deviceFiles = Get-FileHash -LiteralPath $fixture, $settings, (Join-Path $data 'commands.json')
function Assert-DeviceData {
    foreach ($file in $deviceFiles) {
        if ((Get-FileHash -LiteralPath $file.Path).Hash -ne $file.Hash) {
            throw "Upgrade changed device data at $($file.Path)."
        }
    }
}

Invoke-SetupProcess $uninstall 'uninstall-fresh-install'
Assert-Uninstalled
Assert-DeviceData

if (!$PreviousSetup) { $PreviousSetup = $setup }
Invoke-SetupProcess $PreviousSetup 'install-previous-release'
$uninstall = Get-InstalledUninstaller
if ($PreviousSetup -ne $setup -and [version]((Get-Item -LiteralPath $app).VersionInfo.FileVersion) -ge [version]$newVersion) {
    throw 'The upgrade baseline is not older than the new version.'
}
$null = New-Item -Path $runKey -Force
Set-ItemProperty -Path $runKey -Name ClassroomMate -Value $startup

# Hold the same per-user mutex as the tray without accessing classroom hardware.
$userBytes = [Text.Encoding]::UTF8.GetBytes([Environment]::UserName)
$userHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($userBytes)).ToLowerInvariant()
$mutex = [Threading.Mutex]::new($false, "Local\ClassClaw.ClassroomMate.$($userHash.Substring(0, 12))")
try {
    Invoke-SetupProcess $setup 'upgrade-while-running' -ExpectFailure
    Invoke-SetupProcess $uninstall 'uninstall-while-running' -ExpectFailure
} finally {
    $mutex.Dispose()
}

Invoke-SetupProcess $setup 'upgrade' -ExpectedLog 'Previous Classroom Mate uninstaller completed.'
Assert-DeviceData
$uninstall = Get-InstalledUninstaller
if ((Get-Item -LiteralPath $app).VersionInfo.FileVersion -ne $newVersion -or
    (Get-StartupValue) -ne $startup -or (Test-Path -LiteralPath $upgradeState)) {
    throw 'Upgrade did not install the new version, retain startup or clear upgrade state.'
}

Invoke-SetupProcess $uninstall 'uninstall'
Assert-Uninstalled
Assert-DeviceData

# Recreate the footprint of a pre-Setup PowerShell installation.
$null = New-Item -ItemType Directory -Path $target
Copy-Item -LiteralPath (Join-Path $root 'dist\ClassroomMate-win-x64\ClassroomMate.exe') -Destination $app
foreach ($name in @('install.ps1', 'uninstall.ps1', 'old-only.dll')) {
    Set-Content -LiteralPath (Join-Path $target $name) -Value 'legacy test fixture'
}
Invoke-SetupProcess $setup 'upgrade-legacy' -ExpectedLog 'Previous Classroom Mate script installation removed.'
$uninstall = Get-InstalledUninstaller
if ((Test-Path -LiteralPath (Join-Path $target 'old-only.dll')) -or $null -ne (Get-StartupValue)) {
    throw 'Legacy upgrade left an old file or enabled startup.'
}
Assert-DeviceData
Invoke-SetupProcess $uninstall 'uninstall-after-legacy'
Assert-Uninstalled
Remove-Item -LiteralPath $deviceFiles.Path
Remove-Item -LiteralPath $data
Write-Host 'Verified clean install, media imports, published version upgrade, script migration, uninstall, and preservation of device data.'
