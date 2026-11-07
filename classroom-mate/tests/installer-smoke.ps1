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

function Invoke-SetupProcess($file, $operation, [switch]$ExpectFailure) {
    $log = Join-Path ([IO.Path]::GetTempPath()) "classroom-mate-$operation.log"
    $arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$log`""
    $process = Start-Process -FilePath $file -ArgumentList $arguments -PassThru -Wait
    if (($process.ExitCode -ne 0) -ne $ExpectFailure.IsPresent) {
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
        throw "Unexpected exit code $($process.ExitCode) during $operation."
    }
    Write-Host "$operation passed"
}

foreach ($path in @($target, $data, $shortcut, $entry)) {
    if (Test-Path -LiteralPath $path) { throw "Run the installer test with a clean Windows user: $path already exists." }
}
if (Get-ItemPropertyValue $runKey ClassroomMate -ErrorAction SilentlyContinue) {
    throw 'A Classroom Mate startup entry already exists.'
}

Invoke-SetupProcess $setup 'install'
$installed = Get-ItemProperty -LiteralPath $entry
if ($installed.DisplayName -ne 'Classroom Mate' -or $installed.Publisher -ne 'ClassClaw' -or
    $installed.DisplayVersion -ne (Get-Item -LiteralPath $app).VersionInfo.FileVersion -or
    !(Test-Path -LiteralPath $shortcut)) {
    throw 'The Windows uninstall entry or Start menu shortcut is incomplete.'
}
$uninstall = $installed.UninstallString.Trim('"')
$python = Join-Path $target 'runtime\python.exe'
& $python -I -B -c 'import aiortc, av, struct; assert struct.calcsize("P") == 8'
if ($LASTEXITCODE -ne 0) { throw 'The installed x64 media runtime failed to load.' }

$null = New-Item -ItemType Directory -Path $data -Force
$fixture = Join-Path $data 'device.dpapi'
Set-Content -LiteralPath $fixture -Value 'simulated device data'
$originalHash = (Get-FileHash -LiteralPath $fixture).Hash
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

Invoke-SetupProcess $setup 'upgrade'
if ((Get-FileHash -LiteralPath $fixture).Hash -ne $originalHash -or
    (Get-ItemPropertyValue $runKey ClassroomMate) -ne $startup) {
    throw 'Upgrade changed the device data or startup setting.'
}

Invoke-SetupProcess $uninstall 'uninstall'
foreach ($path in @($target, $shortcut, $entry)) {
    if (Test-Path -LiteralPath $path) { throw "Uninstall left $path behind." }
}
if ($null -ne (Get-ItemPropertyValue $runKey ClassroomMate -ErrorAction SilentlyContinue) -or
    (Get-FileHash -LiteralPath $fixture).Hash -ne $originalHash) {
    throw 'Uninstall did not remove startup or preserve the device data.'
}
Remove-Item -LiteralPath $fixture
Remove-Item -LiteralPath $data
Write-Host 'Verified install, media imports, upgrade, uninstall, and preservation of device data.'
