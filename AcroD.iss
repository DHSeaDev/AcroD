; Acro-Data installer (optional). The loose AcroD.exe still works on its own and is what to copy to a
; network share; this script is for people who want Start Menu entries, an uninstaller and the PDF association.
;
; Build: run "BUILD  double click me.bat" first (it writes dist\AcroD.exe), open this file in Inno Setup 6
; (https://jrsoftware.org/isinfo.php), press Compile. Output: installer\AcroD-Setup-1.12.1.exe.
; Per-user: no administrator rights, nothing outside the user's own profile and HKEY_CURRENT_USER.
; [UNVERIFIED] This script has not been compiled or run: do a test install and uninstall on a spare account first.

#define AppName "Acro-Data"
#define AppVersion "1.12.1"
#define AppExe "AcroD.exe"

[Setup]
AppId={{789480D6-AFCE-447B-A5B0-33E17D0AF0BF}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=DHSeaDev
DefaultDirName={autopf}\AcroD
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=AcroD.ico
LicenseFile=LICENSE
OutputDir=installer
OutputBaseFilename=AcroD-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "pdfapp"; Description: "Make {#AppName} able to open PDFs (then choose it on the Windows Default apps page)"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

; The same keys the app writes itself (pdf_handler_entries in acrod.py); uninsdeletekey removes them again.
[Registry]
Root: HKCU; Subkey: "Software\Classes\AcroD.PDF"; ValueType: string; ValueData: "PDF document (Acro-Data)"; Flags: uninsdeletekey; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\AcroD.PDF\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExe}"",0"; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\AcroD.PDF\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: pdfapp
Root: HKCU; Subkey: "Software\AcroD\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: pdfapp
Root: HKCU; Subkey: "Software\AcroD\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Merge, split, shrink, rename and reorganize PDFs. Everything stays on your computer."; Tasks: pdfapp
Root: HKCU; Subkey: "Software\AcroD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "AcroD.PDF"; Tasks: pdfapp
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "AcroD.PDF"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pdfapp
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "AcroD"; ValueData: "Software\AcroD\Capabilities"; Flags: uninsdeletevalue; Tasks: pdfapp

[Run]
Filename: "ms-settings:defaultapps"; Description: "Open Windows' Default apps page to choose {#AppName} for PDF"; Flags: shellexec postinstall skipifsilent unchecked; Tasks: pdfapp
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings, logs and undo records live in %LOCALAPPDATA%\AcroD and are deliberately left behind.

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('Acro-Data was removed. Your settings and logs in %LOCALAPPDATA%\AcroD were kept; delete that folder if you want them gone too.', mbInformation, MB_OK);
end;
