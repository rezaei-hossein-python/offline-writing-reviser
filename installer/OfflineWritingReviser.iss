#define AppName "Offline Writing Reviser"
#define AppExeName "OfflineWritingReviser.exe"
#define AppVersion "0.3.0"
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
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Offline Writing Reviser bootstrap installer
LicenseFile={#ProjectRoot}\LICENSE

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Offline Writing Reviser"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Settings"; Filename: "{app}\{#AppExeName}"; Parameters: "--settings"
Name: "{group}\Diagnostics"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#AppExeName}"" --diagnostics"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall Offline Writing Reviser"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--provision-model"; Description: "Provision the local proofreading model"; Flags: waituntilterminated skipifsilent
Filename: "{app}\{#AppExeName}"; Description: "Start Offline Writing Reviser"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--exit"; RunOnceId: "StopBackgroundApplication"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
const
  OllamaDownloadUrl = 'https://ollama.com/download/OllamaSetup.exe';

function ExistingOllamaPath(): String;
var
  Candidate: String;
begin
  Candidate := ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe');
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;
  Candidate := ExpandConstant('{pf}\Ollama\ollama.exe');
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;
  Result := '';
end;

function OnDownloadProgress(
  const Url, FileName: String;
  const Progress, ProgressMax: Int64): Boolean;
forward;

procedure EnsureOllamaInstalled();
var
  InstallerPath: String;
  ResultCode: Integer;
begin
  if ExistingOllamaPath() <> '' then
  begin
    Log('Reusing existing Ollama installation: ' + ExistingOllamaPath());
    Exit;
  end;

  if MsgBox(
    'Offline Writing Reviser requires Ollama. Setup will download and run ' +
    'the official Ollama installer. An internet connection is required.' +
    Chr(13) + Chr(10) + Chr(13) + Chr(10) + 'Continue?',
    mbConfirmation, MB_YESNO) <> IDYES then
    RaiseException('Ollama installation was declined.');

  InstallerPath := ExpandConstant('{tmp}\OllamaSetup.exe');
  try
    DownloadTemporaryFile(OllamaDownloadUrl, 'OllamaSetup.exe', '', @OnDownloadProgress);
  except
    RaiseException(
      'Ollama could not be downloaded. Check the internet connection and retry.');
  end;
  if not Exec(InstallerPath, '', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
    RaiseException('The official Ollama installer could not be started.');
  if (ResultCode <> 0) or (ExistingOllamaPath() = '') then
    RaiseException('Ollama installation did not complete successfully.');
end;

function OnDownloadProgress(
  const Url, FileName: String;
  const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
    WizardForm.StatusLabel.Caption :=
      Format('Downloading Ollama: %d%%', [Progress * 100 div ProgressMax])
  else
    WizardForm.StatusLabel.Caption := 'Downloading Ollama...';
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    EnsureOllamaInstalled();
end;
