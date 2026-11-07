#define Payload "..\dist\ClassroomMate-win-x64"
#define ProductVersion GetVersionNumbersString(Payload + "\ClassroomMate.exe")
#define InstallDir "{localappdata}\Programs\ClassroomMate"

[Setup]
AppId=ClassClaw.ClassroomMate
AppName=Classroom Mate
AppVersion={#ProductVersion}
AppPublisher=ClassClaw
VersionInfoVersion={#ProductVersion}
VersionInfoCompany=ClassClaw
VersionInfoDescription=Classroom Mate Setup
DefaultDirName={#InstallDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.14393
WizardStyle=modern
SetupIconFile=..\assets\ClassroomMate.ico
OutputDir=..\dist
OutputBaseFilename=ClassroomMate-Setup-win-x64
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Classroom Mate
UninstallDisplayIcon={app}\ClassroomMate.exe
AppMutex={code:TrayMutex}
SetupMutex=Local\ClassClaw.ClassroomMate.Setup
CloseApplications=no

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "\install.ps1,\uninstall.ps1,\SHA256SUMS.json"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{userprograms}\Classroom Mate"; Filename: "{app}\ClassroomMate.exe"; WorkingDir: "{app}"

[Registry]
; The tray owns the setting. An upgrade restores it only when it was previously enabled.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ClassroomMate"; Flags: dontcreatekey uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClassroomMate"; ValueData: """{app}\ClassroomMate.exe"" --tray"; Check: RestoreStartup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ClassroomMate.exe"; Description: "Launch Classroom Mate"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Messages]
SetupAppRunningError=%1 正在运行。请右键任务栏通知区域的 Classroom Mate 图标，选择「退出」，然后点击 OK 继续。

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassClaw.ClassroomMate_is1';
  StartupKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  UpgradeKey = 'Software\ClassClaw\ClassroomMate\Setup';
  RemainingFiles = '旧程序目录仍有文件。请关闭正在占用该目录的程序，或移出自己存放的文件，再运行安装包。';

var
  KeepStartup: Boolean;

function TrayMutex(Param: String): String;
begin
  // Keep in sync with Program.Main, including the encoding of Windows user names.
  Result := 'Local\ClassClaw.ClassroomMate.' +
    Copy(Lowercase(GetSHA256OfString(UTF8Encode(GetUserNameString))), 1, 12);
end;

function LegacyInstall(const Dir: String): Boolean;
begin
  Result := FileExists(Dir + '\ClassroomMate.exe') and FileExists(Dir + '\install.ps1') and FileExists(Dir + '\uninstall.ps1');
end;

function PlainPath(const Path: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := True;
  if FindFirst(Path, Entry) then begin
    Result := (Entry.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0;
    FindClose(Entry);
  end;
end;

function HasFiles(const Dir: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := False;
  if FindFirst(Dir + '\*', Entry) then begin
    repeat
      Result := (Entry.Name <> '.') and (Entry.Name <> '..');
    until Result or not FindNext(Entry);
    FindClose(Entry);
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Version: String;
begin
  Result := '安装位置：' + NewLine + Space + ExpandConstant('{app}');
  if RegKeyExists(HKCU, UninstallKey) or LegacyInstall(ExpandConstant('{app}')) then begin
    RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Version);
    Result := '已发现旧版 Classroom Mate ' + Version + NewLine +
      '点击 Install 后将先移除旧版程序，再安装 {#ProductVersion}。配对、设备设置及登录自启选项会保留。' +
      NewLine + NewLine + Result;
  end;
end;

function RemoveRegisteredInstall(const Dir: String): String;
var
  Exe, Name: String;
  ResultCode, Attempt: Integer;
begin
  Result := '旧版卸载入口无效。请在 Windows「已安装的应用」修复或移除 Classroom Mate 后重试。';
  if not RegQueryStringValue(HKCU, UninstallKey, 'UninstallString', Exe) then Exit;
  Exe := RemoveQuotes(Trim(Exe));
  Name := Lowercase(ExtractFileName(Exe));
  if (CompareText(ExtractFileDir(Exe), Dir) <> 0) or (Length(Name) <> 12) or
    (Copy(Name, 1, 5) <> 'unins') or (Copy(Name, 9, 4) <> '.exe') or
    (StrToIntDef(Copy(Name, 6, 3), -1) < 0) or not FileExists(Exe) or not PlainPath(Exe) then Exit;

  Result := '旧版卸载未完成。请退出 Classroom Mate，检查 Windows 的卸载提示后重试。';
  if not Exec(Exe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG', ExpandConstant('{tmp}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then Exit;
  if ResultCode <> 0 then Exit;

  // Inno's first process exits before its temporary copy deletes the uninstaller.
  for Attempt := 1 to 50 do begin
    if not FileExists(Exe) then Break;
    Sleep(200);
  end;
  if FileExists(Exe) or RegKeyExists(HKCU, UninstallKey) then Exit;
  if HasFiles(Dir) then begin
    Result := RemainingFiles;
    Exit;
  end;
  Log('Previous Classroom Mate uninstaller completed.');
  Result := '';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Dir: String;
  Registered, Legacy: Boolean;
  InstalledVersion, NewVersion: Int64;
begin
  Dir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  if (CompareText(Dir, ExpandConstant('{#InstallDir}')) <> 0) or not PlainPath(Dir) then begin
    Result := '请使用 Classroom Mate 的默认安装目录。';
    Exit;
  end;
  if CheckForMutexes(TrayMutex('')) then begin
    Result := '请先从通知区域退出 Classroom Mate，然后重试。';
    Exit;
  end;
  if GetPackedVersion(Dir + '\ClassroomMate.exe', InstalledVersion) and
    StrToVersion('{#ProductVersion}', NewVersion) and (ComparePackedVersion(InstalledVersion, NewVersion) > 0) then begin
    Result := '已安装更新版本的 Classroom Mate，请使用对应版本的安装包。';
    Exit;
  end;

  Registered := RegKeyExists(HKCU, UninstallKey);
  Legacy := LegacyInstall(Dir);
  KeepStartup := RegValueExists(HKCU, UpgradeKey, 'RestoreStartup');
  if Registered or Legacy then begin
    KeepStartup := KeepStartup or RegValueExists(HKCU, StartupKey, 'ClassroomMate');
    // Preserve the choice even if the user has to rerun Setup after the old copy is removed.
    if KeepStartup and not RegWriteDWordValue(HKCU, UpgradeKey, 'RestoreStartup', 1) then begin
      Result := '无法保存登录自启设置，旧版尚未移除。请检查当前 Windows 账号的权限。';
      Exit;
    end;
  end;

  Result := '';
  if Registered then
    Result := RemoveRegisteredInstall(Dir)
  else if Legacy then begin
    if not DelTree(Dir, True, True, True) then
      Result := RemainingFiles
    else begin
      RegDeleteValue(HKCU, StartupKey, 'ClassroomMate');
      Log('Previous Classroom Mate script installation removed.');
    end;
  end else if HasFiles(Dir) then
    Result := RemainingFiles;
end;

function RestoreStartup: Boolean;
begin
  Result := KeepStartup;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then begin
    RegDeleteValue(HKCU, UpgradeKey, 'RestoreStartup');
    RegDeleteKeyIfEmpty(HKCU, UpgradeKey);
  end;
end;
