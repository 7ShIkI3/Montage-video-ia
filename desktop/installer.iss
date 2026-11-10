; Installateur Windows de Monteur IA (Inno Setup 6).
; Ce fichier est en UTF-8 AVEC BOM : sans lui, Inno Setup lirait les accents dans la page de code de Windows.
;
;   python desktop/build.py --ffmpeg CHEMIN --installer     construit l'application puis l'installateur
;   python desktop/build.py --installer-only                 remballe dist\Monteur IA tel quel
;
; Résultat : dist\installer\MonteurIA-Setup-<version>.exe. Un seul fichier, sans droits d'administrateur :
; l'application va dans le dossier de l'utilisateur (%LOCALAPPDATA%\Programs\Monteur IA), avec ses raccourcis
; et sa désinstallation dans « Applications installées ». Les projets (Documents\Monteur IA) ne sont jamais touchés.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Monteur IA"
#define AppExe "Monteur IA.exe"
#define Source "..\dist\Monteur IA"

[Setup]
; AppId identifie l'application d'une version à l'autre : ne jamais le changer, sinon une mise à jour
; s'installerait à côté de l'ancienne au lieu de la remplacer.
AppId={{8C3D1F2A-5B7E-4A6C-9D21-6F0E3B7A41C5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Installation de {#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=monteur.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
; Une mise à jour ferme l'application si elle tourne, puis la remplace.
CloseApplications=yes
RestartApplications=no
OutputDir=..\dist\installer
OutputBaseFilename=MonteurIA-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[InstallDelete]
; Mise à jour : on repart d'un dossier d'application propre. Un module Python d'une ancienne version resté là
; serait chargé à la place du nouveau.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Excludes: "\_internal\nvidia,\_internal\nvidia\*"; Flags: recursesubdirs createallsubdirs ignoreversion
; Bibliothèques NVIDIA (2 Go) : elles ne servent qu'à transcrire sur une carte graphique NVIDIA. Sans cette
; carte, on ne les copie pas et la transcription se fait sur le processeur.
Source: "{#Source}\_internal\nvidia\*"; DestDir: "{app}\_internal\nvidia"; Check: HasNvidia; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Une carte NVIDIA avec son pilote installe nvcuda64.dll (ou nvcuda.dll) dans le dossier système.
function HasNvidia(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\nvcuda64.dll')) or FileExists(ExpandConstant('{sys}\nvcuda.dll'));
end;

// La désinstallation retire l'application, pas le travail de l'utilisateur : on le lui dit.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Monteur IA est désinstallé.' + #13#10 + #13#10 +
           'Vos projets et vos exports sont conservés dans Documents\Monteur IA.', mbInformation, MB_OK);
end;
