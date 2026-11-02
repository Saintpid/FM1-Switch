; Windows installer for FM-1 Switch, in the style of the Noisebud app installers (Pre Flight, PER):
; Inno Setup's modern wizard, the licence to accept, Noisebud as publisher, a finish page that offers
; to start the app. FM-1 Switch is free software (GPL-3.0): no registration, no licence manifest; its
; licence page is installer\EULA.txt (no warranty, no liability for any hardware).
; Built by scripts\build-installer.ps1.

#ifndef AppVer
  #error AppVer must contain the release version.
#endif
#ifndef AppSrc
  #error AppSrc must point to PyInstaller's FM1-Switch folder.
#endif
#ifndef EulaFile
  #error EulaFile must point to installer\EULA.txt.
#endif
#ifndef IconFile
  #error IconFile must point to the app icon (.ico).
#endif
#ifndef DocDir
  #error DocDir must point to the folder with LICENSE and DISCLAIMER.md.
#endif
#ifndef OutDir
  #error OutDir must point to the installer output directory.
#endif
#ifndef OutputBaseFilename
  #error OutputBaseFilename must contain the setup filename without extension.
#endif

#define ProductName "FM-1 Switch"
#define ExeName "FM1-Switch.exe"

[Setup]
AppId=NoisebudApp-fm1switch
AppName={#ProductName}
AppVersion={#AppVer}
AppVerName={#ProductName} v{#AppVer}
AppPublisher=Noisebud
AppPublisherURL=https://github.com/Saintpid/FM1-Switch
AppSupportURL=https://github.com/Saintpid/FM1-Switch
VersionInfoVersion={#AppVer}
VersionInfoCompany=Noisebud
VersionInfoDescription={#ProductName} Windows Setup
VersionInfoProductName={#ProductName}
VersionInfoCopyright=Free software, GPL-3.0. No warranty.
DefaultDirName={autopf}\Noisebud\{#ProductName}
DefaultGroupName=Noisebud
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename={#OutputBaseFilename}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
Uninstallable=yes
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#ProductName}
SetupIconFile={#IconFile}
WizardStyle=modern
LicenseFile={#EulaFile}
CloseApplications=yes
RestartIfNeededByRun=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#AppSrc}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#EulaFile}"; DestDir: "{app}"; DestName: "License and Disclaimer.txt"; Flags: ignoreversion
Source: "{#DocDir}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#DocDir}\DISCLAIMER.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; An update replaces the whole app; files from an older version must not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\{#ExeName}"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "Start FM-1 Switch now"; Flags: postinstall skipifsilent runasoriginaluser nowait

[Code]
var
  RiskNote: TNewStaticText;

procedure InitializeWizard;
begin
  WizardForm.RunList.Height := ScaleY(30);
  RiskNote := TNewStaticText.Create(WizardForm.FinishedPage);
  RiskNote.Parent := WizardForm.FinishedPage;
  RiskNote.Left := WizardForm.RunList.Left + ScaleX(2);
  RiskNote.Top := WizardForm.RunList.Top + WizardForm.RunList.Height + ScaleY(12);
  RiskNote.Width := WizardForm.RunList.Width - ScaleX(4);
  RiskNote.Height := ScaleY(48);
  RiskNote.AutoSize := False;
  RiskNote.WordWrap := True;
  RiskNote.Caption :=
    'FM-1 Switch is free software. Everything it does to your FM-1 is at your own risk: ' +
    'neither the author nor Noisebud takes any responsibility for your hardware.';
end;
