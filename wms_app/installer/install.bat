@echo off
chcp 65001 >nul
echo ========================================
echo      进销存管理系统 安装程序
echo ========================================
echo.

set "APP_DIR=C:\Program Files\进销存管理系统"
set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

:: 检查管理员权限
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [提示] 建议右键选择"以管理员身份运行"以获得最佳安装体验
    echo.
)

:: 创建安装目录
if not exist "%APP_DIR%" (
    mkdir "%APP_DIR%" 2>nul
    if errorlevel 1 (
        echo [错误] 无法创建安装目录，请检查权限！
        pause
        exit /b 1
    )
)

:: 复制主程序
echo [1/3] 正在安装主程序...
copy /y "进销存管理系统.exe" "%APP_DIR%\" >nul
if errorlevel 1 (
    echo [错误] 复制文件失败！
    pause
    exit /b 1
)

:: 创建桌面快捷方式
echo [2/3] 正在创建桌面快捷方式...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\进销存管理系统.lnk'); $s.TargetPath = '%APP_DIR%\进销存管理系统.exe'; $s.WorkingDirectory = '%APP_DIR%'; $s.IconLocation = '%APP_DIR%\进销存管理系统.exe'; $s.Save()"

:: 创建开始菜单快捷方式
echo [3/3] 正在创建开始菜单...
if not exist "%STARTMENU%\进销存管理系统" mkdir "%STARTMENU%\进销存管理系统"
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTMENU%\进销存管理系统\进销存管理系统.lnk'); $s.TargetPath = '%APP_DIR%\进销存管理系统.exe'; $s.WorkingDirectory = '%APP_DIR%'; $s.IconLocation = '%APP_DIR%\进销存管理系统.exe'; $s.Save()"

echo.
echo ========================================
echo      安装完成！
echo ========================================
echo.
echo   安装位置: %APP_DIR%
echo   桌面已创建快捷方式
echo.
set /p RUN="   是否立即运行程序？(Y/N): "
if /i "%RUN%"=="Y" (
    start "" "%APP_DIR%\进销存管理系统.exe"
)
echo.
exit /b 0
