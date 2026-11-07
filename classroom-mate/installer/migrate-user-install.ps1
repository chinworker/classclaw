# Run in the account that started Setup, including when UAC used another account.
$ErrorActionPreference = 'Stop'
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$marker = 'HKCU:\Software\ClassClaw\ClassroomMate\Setup'
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassClaw.ClassroomMate_is1'
$target = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\ClassroomMate'
$app = Join-Path $target 'ClassroomMate.exe'

try {
    if ($finalizeUpgrade) {
        if ((Get-ItemProperty -LiteralPath $marker -ErrorAction SilentlyContinue).RestoreStartup) {
            $programs = [Environment]::GetFolderPath('ProgramFiles')
            $newApp = Join-Path $programs 'ClassroomMate\ClassroomMate.exe'
            if (!(Test-Path -LiteralPath $newApp -PathType Leaf)) { exit 11 }
            $null = New-Item -Path $run -Force
            Set-ItemProperty -LiteralPath $run -Name ClassroomMate -Value ('"' + $newApp + '" --tray')
            Remove-ItemProperty -LiteralPath $marker -Name RestoreStartup
        }
        exit 0
    }

    $sha = [Security.Cryptography.SHA256]::Create()
    try { $userHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([Environment]::UserName))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    $existing = $null
    if ([Threading.Mutex]::TryOpenExisting("Local\ClassClaw.ClassroomMate.$($userHash.Substring(0, 12))", [ref]$existing)) {
        $existing.Dispose()
        exit 10
    }
    $installed = Test-Path -LiteralPath $key
    $legacy = (Test-Path -LiteralPath $app -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $target 'install.ps1') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $target 'uninstall.ps1') -PathType Leaf)
    if (!$installed -and !$legacy) {
        if (Test-Path -LiteralPath $app) { exit 11 }
        exit 0
    }

    if ((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { exit 11 }
    if ($installed) {
        $uninstall = (Get-ItemProperty -LiteralPath $key).UninstallString.Trim('"')
        if ([IO.Path]::GetDirectoryName($uninstall) -ne $target -or [IO.Path]::GetFileName($uninstall) -notmatch '^unins[0-9]{3}\.exe$' -or
            !(Test-Path -LiteralPath $uninstall -PathType Leaf) -or
            ((Get-Item -LiteralPath $uninstall -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { exit 11 }
    }

    $startup = (Get-ItemProperty -LiteralPath $run -ErrorAction SilentlyContinue).ClassroomMate
    if ($startup) {
        $null = New-Item -Path $marker -Force
        Set-ItemProperty -LiteralPath $marker -Name RestoreStartup -Value 1
    }

    if ($installed) {
        $process = Start-Process -FilePath $uninstall -WorkingDirectory $env:WINDIR -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -PassThru -Wait
        if ($process.ExitCode -ne 0 -or (Test-Path -LiteralPath $key) -or (Test-Path -LiteralPath $app)) { exit 11 }
    } else {
        [IO.Directory]::Delete($target, $true)
        Remove-ItemProperty -LiteralPath $run -Name ClassroomMate -ErrorAction SilentlyContinue
        $shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Classroom Mate.lnk'
        if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut }
    }
    exit 0
} catch {
    exit 11
}
