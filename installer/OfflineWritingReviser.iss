#define AppName "Offline Writing Reviser"
#define AppExeName "OfflineWritingReviser.exe"
#define AppVersion "0.3.1-rc2"
#define AppNumericVersion "0.3.1.1"
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
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Offline Writing Reviser bootstrap installer
LicenseFile={#ProjectRoot}\LICENSE

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Offline Writing Reviser"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Settings"; Filename: "{app}\{#AppExeName}"; Parameters: "--settings"
Name: "{group}\Set up AI proofreading"; Filename: "{app}\{#AppExeName}"; Parameters: "--provision-model"
Name: "{group}\Diagnostics"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#AppExeName}"" --diagnostics"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall Offline Writing Reviser"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "OfflineWritingReviser"; \
    ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start Offline Writing Reviser"; Flags: nowait runhidden
Filename: "{app}\{#AppExeName}"; Parameters: "--provision-model"; Description: "Set up optional AI proofreading (can be retried later)"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--exit"; RunOnceId: "StopBackgroundApplication"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
// Core setup never downloads OllamaSetup.exe or waits for a model download.
// Optional AI setup runs only after setup has completed and is also available
// from the Start menu, so interruption never prevents LanguageTool operation.
