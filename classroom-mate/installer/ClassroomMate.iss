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
; The tray app owns the login startup setting. Only remove its value at uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ClassroomMate"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\ClassroomMate.exe"; Description: "Launch Classroom Mate"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function TrayMutex(Param: String): String;
begin
  // Keep in sync with Program.Main, including the encoding of Windows user names.
  Result := 'Local\ClassClaw.ClassroomMate.' +
    Copy(Lowercase(GetSHA256OfString(UTF8Encode(GetUserNameString))), 1, 12);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')), ExpandConstant('{#InstallDir}')) <> 0 then
    Result := 'Install Classroom Mate to the default per-user directory.'
  else if FileExists(ExpandConstant('{app}\install.ps1')) then
    Result := 'Remove the previous script installation with its uninstall.ps1 before running Setup. Keep the device data.'
  else if CheckForMutexes(TrayMutex('')) then
    Result := 'Exit Classroom Mate from the notification area before continuing.';
end;
