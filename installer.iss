[Setup]
AppName=무좀사진분류기
AppVersion=1.0
AppPublisher=peppermint1231
DefaultDirName={autopf}\무좀사진분류기
DefaultGroupName=무좀사진분류기
OutputDir=installer_output
OutputBaseFilename=무좀사진분류기_설치
Compression=lzma2
SolidCompression=yes
SetupIconFile=toenail_classifier_icon.ico
UninstallDisplayName=무좀사진분류기
PrivilegesRequired=lowest

[Files]
Source: "dist\무좀사진분류기\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\무좀사진분류기"; Filename: "{app}\무좀사진분류기.exe"
Name: "{group}\무좀사진분류기 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\무좀사진분류기"; Filename: "{app}\무좀사진분류기.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 생성"; GroupDescription: "추가 옵션:"

[Run]
Filename: "{app}\무좀사진분류기.exe"; Description: "무좀사진분류기 실행"; Flags: nowait postinstall skipifsilent
