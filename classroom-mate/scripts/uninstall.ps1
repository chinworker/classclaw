param([switch]$RemoveDeviceData)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ClassroomMate -ErrorAction SilentlyContinue) { throw '请先从托盘正常退出 Classroom Mate。' }
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name ClassroomMate -ErrorAction SilentlyContinue
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'Classroom Mate.lnk'
if (Test-Path $link) { Remove-Item -LiteralPath $link }
$target = Join-Path $env:LOCALAPPDATA 'Programs\ClassroomMate'
if (Test-Path $target) { Remove-Item -LiteralPath $target -Recurse -Force }
if ($RemoveDeviceData) {
    $data = Join-Path $env:LOCALAPPDATA 'ClassClaw\ClassroomMate'
    if (Test-Path $data) { Remove-Item -LiteralPath $data -Recurse -Force }
    Write-Host '设备本地数据已移除；请在网页撤销旧设备后重新配对。'
} else { Write-Host '已卸载，保留加密配对凭据和去重账本供重装使用。' }
