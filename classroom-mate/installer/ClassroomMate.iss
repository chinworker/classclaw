#define Payload "..\dist\ClassroomMate-win-x64"
#define ProductVersion GetVersionNumbersString(Payload + "\ClassroomMate.exe")
#define InstallDir "{autopf}\ClassroomMate"

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
PrivilegesRequired=admin
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
Name: "{commonprograms}\Classroom Mate"; Filename: "{app}\ClassroomMate.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\ClassroomMate.exe"; Description: "Launch Classroom Mate"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Messages]
SetupAppRunningError=%1 正在运行。请右键任务栏通知区域的 Classroom Mate 图标，选择「退出」，然后点击 OK 继续。

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\ClassClaw.ClassroomMate_is1';
  StartupKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RemainingFiles = '旧程序目录仍有文件。请关闭正在占用该目录的程序，或移出自己存放的文件，再运行安装包。';

function TrayMutex(Param: String): String;
begin
  // Keep in sync with Program.Main, including the encoding of Windows user names.
  Result := 'Local\ClassClaw.ClassroomMate.' +
    Copy(Lowercase(GetSHA256OfString(UTF8Encode(GetUserNameString))), 1, 12);
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
begin
  Result := '若发现旧版 Classroom Mate，将先移除旧程序，再安装 {#ProductVersion}。配对及设备设置会保留。' +
    NewLine + NewLine + '安装位置：' + NewLine + Space + ExpandConstant('{app}');
end;

function RemoveRegisteredInstall(const Dir: String): String;
var
  Exe, Name: String;
  ResultCode, Attempt: Integer;
begin
  Result := '旧版卸载入口无效。请在 Windows「已安装的应用」修复或移除 Classroom Mate 后重试。';
  if not RegQueryStringValue(HKLM64, UninstallKey, 'UninstallString', Exe) then Exit;
  Exe := RemoveQuotes(Trim(Exe));
  Name := Lowercase(ExtractFileName(Exe));
  if (CompareText(ExtractFileDir(Exe), Dir) <> 0) or (Length(Name) <> 12) or
    (Copy(Name, 1, 5) <> 'unins') or (Copy(Name, 9, 4) <> '.exe') or
    (StrToIntDef(Copy(Name, 6, 3), -1) < 0) or not FileExists(Exe) or not PlainPath(Exe) then Exit;

  Result := '旧版卸载未完成。请退出 Classroom Mate，检查 Windows 的卸载提示后重试。';
  if not Exec(Exe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG /CMUPGRADE', ExpandConstant('{tmp}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then Exit;
  if ResultCode <> 0 then Exit;

  // Inno's first process exits before its temporary copy deletes the uninstaller.
  for Attempt := 1 to 50 do begin
    if not FileExists(Exe) then Break;
    Sleep(200);
  end;
  if FileExists(Exe) or RegKeyExists(HKLM64, UninstallKey) then Exit;
  if HasFiles(Dir) then begin
    Result := RemainingFiles;
    Exit;
  end;
  Log('Previous Classroom Mate uninstaller completed.');
  Result := '';
end;

function RunUserMigration(const Encoded: String): Integer;
var
  Code: Integer;
begin
  if ExecAsOriginalUser(ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + Encoded, ExpandConstant('{win}'),
      SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Result := 11;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Dir: String;
  Code: Integer;
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

  Result := '';
  if RegKeyExists(HKLM64, UninstallKey) then
    Result := RemoveRegisteredInstall(Dir)
  else if HasFiles(Dir) then
    Result := RemainingFiles;
  if Result <> '' then Exit;

  Code := RunUserMigration('{#PrepareUser}');
  if Code = 10 then
    Result := '请先从通知区域退出 Classroom Mate，然后重新运行安装程序。'
  else if Code <> 0 then
    Result := '无法移除原 Windows 用户的旧版程序。请用原安装账号检查旧版卸载入口和目录权限。'
  else
    Log('Classroom Mate user migration checked.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if RunUserMigration('{#FinishUser}') <> 0 then
      SuppressibleMsgBox('新版已安装。若此前启用了登录自启，请在 Classroom Mate 设置中重新确认。', mbInformation, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Users: TArrayOfString;
  Key, Value, Expected: String;
  Index: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), '/CMUPGRADE') = 0 then Exit;
  Expected := '"' + ExpandConstant('{app}\ClassroomMate.exe') + '" --tray';
  if RegGetSubkeyNames(HKU, '', Users) then
    for Index := 0 to GetArrayLength(Users) - 1 do begin
      Key := Users[Index] + '\' + StartupKey;
      if RegQueryStringValue(HKU, Key, 'ClassroomMate', Value) and (CompareText(Value, Expected) = 0) then
        RegDeleteValue(HKU, Key, 'ClassroomMate');
    end;
end;
