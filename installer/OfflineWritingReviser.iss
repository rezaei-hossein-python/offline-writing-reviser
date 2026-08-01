#define AppName "Offline Writing Reviser"
#define AppExeName "OfflineWritingReviser.exe"
#define AppVersion "0.4.0"
#define AppNumericVersion "0.4.0.0"
#define ProjectRoot AddBackslash(SourcePath) + ".."
#define AppBuildDir ProjectRoot + "\dist\OfflineWritingReviser"

[Setup]
AppId={{5A04995D-615A-46AF-9EEA-ACD8A95BBABC}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Offline Writing Reviser
DefaultDirName={localappdata}\Programs\Offline Writing Reviser
DefaultGroupName=Offline Writing Reviser
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ProjectRoot}\dist\installer
OutputBaseFilename=OfflineWritingReviser-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Offline Writing Reviser bootstrap installer
LicenseFile={#ProjectRoot}\LICENSE

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Offline Writing Reviser"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Settings"; Filename: "{app}\{#AppExeName}"; Parameters: "--settings"
Name: "{group}\Set up intelligent revision"; Filename: "{app}\{#AppExeName}"; Parameters: "--provision-model"
Name: "{group}\Uninstall Offline Writing Reviser"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "OfflineWritingReviser"; \
    ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start Offline Writing Reviser"; Flags: nowait runhidden
Filename: "{app}\{#AppExeName}"; Parameters: "--provision-model"; Description: "Set up intelligent revision (can be retried later)"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--exit"; RunOnceId: "StopBackgroundApplication"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
// Core setup never downloads OllamaSetup.exe or waits for a model download.
// Model setup runs only after setup has completed and remains retryable from
// the Start menu, so interruption never corrupts the application installation.

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ExistingExe: String;
begin
  Result := '';
  ExistingExe := ExpandConstant('{app}\{#AppExeName}');
  if FileExists(ExistingExe) then
    Exec(ExistingExe, '--exit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
