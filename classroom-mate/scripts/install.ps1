# Per-user install; run from an extracted release folder. No administrator rights.
$ErrorActionPreference = 'Stop'
$target = Join-Path $env:LOCALAPPDATA 'Programs\ClassroomMate'
$exe = Join-Path $target 'ClassroomMate.exe'
$running = Get-Process -Name ClassroomMate -ErrorAction SilentlyContinue
if ($running) { throw '请先从 Classroom Mate 托盘正常退出，再安装或升级。' }
if (!(Test-Path (Join-Path $PSScriptRoot 'ClassroomMate.exe'))) { throw '请从完整发布包中运行安装脚本。' }
if ([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($target)) {
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -LiteralPath $PSScriptRoot | Copy-Item -Destination $target -Recurse -Force
}
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
New-Item -Path $run -Force | Out-Null
New-ItemProperty -Path $run -Name ClassroomMate -Value ('"' + $exe + '" --tray') -PropertyType String -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Classroom Mate.lnk'))
$link.TargetPath = $exe
$link.WorkingDirectory = $target
$link.Save()
Start-Process -FilePath $exe -WorkingDirectory $target
Write-Host '安装完成；登录后自动驻留托盘。'
