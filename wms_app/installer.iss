; ─────────────────────────────────────────────────────────────
; 进销存管理系统 — Windows 安装包脚本 (Inno Setup 6)
; 用法：用 Inno Setup Compiler 打开此文件，点「编译」即可生成安装包
; 或直接用命令行：ISCC.exe installer.iss
; ─────────────────────────────────────────────────────────────
#define MyAppName "进销存管理系统"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "WMS"
#define MyAppURL "http://127.0.0.1:8899"
#define MyAppExeName "进销存管理系统.exe"

[Setup]
; 基本设置
AppId={{8F3C2A1B-6D4E-4F2A-9C1B-7E5D3A8B2C4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; 安装到当前用户目录（localappdata\Programs），普通用户可写，无需管理员权限
; 安装过程中会显示"选择目标位置"页，用户可自由选择任意目录（含其他盘符）
DefaultDirName={localappdata}\Programs\{#MyAppName}
; 显式开启"选择目标位置"页，允许用户自由选择安装目录
DisableDirPage=no
; 允许保留默认目录（不强制必须改）
AppendDefaultDirName=no
DefaultGroupName={#MyAppName}
; 安装包输出（相对于本脚本所在目录 ../installer）
OutputDir=..\installer
OutputBaseFilename=进销存管理系统安装包_v{#MyAppVersion}
; 外观
SetupIconFile=static\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
; 不需要管理员权限：数据库默认落在程序目录，普通用户可写
PrivilegesRequired=lowest
; 安装时若旧版本正在运行，自动关闭占用 exe 的进程；装完不重复重启，仅由下方 [Run] 启动一次
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=auto

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Files]
; 主程序（PyInstaller 单文件 exe）
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 发票文字 OCR 引擎（Tesseract，离线识别无二维码发票时兜底）
Source: "tesseract\*"; DestDir: "{app}\tesseract"; Flags: ignoreversion recursesubdirs

[Icons]
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"; Flags: checkedonce

[Run]
; 安装完成后启动程序
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时同时清理数据库（如需保留数据，请注释掉此行）
Type: filesandordirs; Name: "{app}\wms_data.db"
Type: filesandordirs; Name: "{app}\wms_data.db-wal"
Type: filesandordirs; Name: "{app}\wms_data.db-shm"
Type: filesandordirs; Name: "{app}\wms.log"
