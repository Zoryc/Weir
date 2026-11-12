; Inno Setup script for Weir (per-user install, no admin rights needed).
; Build with build_installer.bat, which builds the app first and passes AppVersion.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Weir"
#define AppExe "Weir.exe"
#define AppUserModelID "Weir.App"
#define SourceDir "..\dist\Weir"
#define AssetsDir "..\build\installer"

[Setup]
; Never change AppId: Windows uses it to recognise upgrades and the uninstaller.
AppId={{A28D8DF2-CCC1-4DBA-AFCD-11B7583E463F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppComments=Download videos and music from a link — a simple window for yt-dlp.
VersionInfoVersion={#AppVersion}
; Installs for the current user only: %LOCALAPPDATA%\Programs\Weir, no UAC prompt.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
WizardSizePercent=100
SetupIconFile={#AssetsDir}\icon.ico
; Several sizes: Setup picks the one matching the screen's DPI (100/150/200 %).
WizardImageFile={#AssetsDir}\wizard.bmp,{#AssetsDir}\wizard-150.bmp,{#AssetsDir}\wizard-200.bmp
WizardSmallImageFile={#AssetsDir}\wizard-small.bmp,{#AssetsDir}\wizard-small-150.bmp,{#AssetsDir}\wizard-small-200.bmp
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
OutputDir=..\dist
OutputBaseFilename=Weir-Setup-{#AppVersion}
; Close a running Weir before upgrading or uninstalling.
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
english.LaunchAfter=Open Weir now
french.LaunchAfter=Ouvrir Weir maintenant
english.ToolsNote=On first start, Weir offers to download the free tools it uses (yt-dlp, ffmpeg and Deno) with one click.
french.ToolsNote=Au premier lancement, Weir propose de télécharger en un clic les outils gratuits qu'il utilise (yt-dlp, ffmpeg et Deno).

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The app itself. The bin\ folder is left out on purpose: the tools are downloaded on first start.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\bin\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Where the app downloads yt-dlp, ffmpeg and Deno (writable: this is a per-user install).
Name: "{app}\bin"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchAfter}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Tools downloaded by the app after installation.
Type: filesandordirs; Name: "{app}\bin"

[Code]
procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption := WizardForm.WelcomeLabel2.Caption + #13#10#13#10 + CustomMessage('ToolsNote');
end;
