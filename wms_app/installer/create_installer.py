# -*- coding: utf-8 -*-
"""
进销存管理系统 安装程序
- 解压到 Program Files
- 创建桌面快捷方式
- 创建开始菜单
"""
import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

# 获取脚本所在目录（安装包解压后在这里）
SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

def is_admin():
    """检查是否有管理员权限"""
    try:
        return subprocess.check_output('net session', stderr=subprocess.DEVNULL, shell=True) == b''
    except:
        return False

def create_shortcut(target_path, shortcut_path, description="", work_dir=None):
    """创建快捷方式"""
    try:
        import pythoncom
        from win32com.shell.shortcut import Shortcut
        from win32com.shell.shell import ShellLink
        
        pythoncom.CoInitialize()
        
        shortcut = pythoncom.CoCreateInstance(
            ShellLink, None,
            pythoncom.CLSID_ShellLink
        )
        shortcut.SetPath(str(target_path))
        if description:
            shortcut.SetDescription(description)
        if work_dir:
            shortcut.SetWorkingDirectory(str(work_dir))
        
        # 保存快捷方式
        with open(shortcut_path, 'wb') as f:
            shortcut.QueryInterface(pythoncom.IPropertySetStorage).Commit()
        
        pythoncom.CoUninitialize()
        return True
    except Exception as e:
        print(f"创建快捷方式失败: {e}")
        return False

def install_app():
    """执行安装"""
    print("=" * 50)
    print("     进销存管理系统 安装程序")
    print("=" * 50)
    print()
    
    # 查找主程序（在同一目录下）
    exe_file = SCRIPT_DIR / "进销存管理系统.exe"
    if not exe_file.exists():
        print(f"[错误] 找不到主程序: {exe_file}")
        input("按回车键退出...")
        return False
    
    # 安装目录
    install_dir = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "进销存管理系统"
    
    print(f"[1/4] 创建安装目录...")
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[错误] 无法创建目录 {install_dir}: {e}")
        if not is_admin():
            print("提示: 请尝试右键选择'以管理员身份运行'")
        input("按回车键退出...")
        return False
    
    print(f"[2/4] 复制文件到 {install_dir}...")
    try:
        shutil.copy2(exe_file, install_dir)
    except Exception as e:
        print(f"[错误] 复制文件失败: {e}")
        input("按回车键退出...")
        return False
    
    print(f"[3/4] 创建桌面快捷方式...")
    desktop = Path.home() / "Desktop"
    shortcut_path = desktop / "进销存管理系统.lnk"
    
    # 使用 PowerShell 创建快捷方式
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("{shortcut_path}")
$shortcut.TargetPath = "{install_dir / '进销存管理系统.exe'}"
$shortcut.WorkingDirectory = "{install_dir}"
$shortcut.Description = "进销存管理系统"
$shortcut.Save()
'''
    subprocess.run(['powershell', '-Command', ps_script], capture_output=True)
    
    print(f"[4/4] 创建开始菜单...")
    start_menu = Path(os.environ.get("APPDATA")) / "Microsoft\\Windows\\Start Menu\\Programs"
    app_menu = start_menu / "进销存管理系统"
    app_menu.mkdir(parents=True, exist_ok=True)
    
    start_shortcut = app_menu / "进销存管理系统.lnk"
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("{start_shortcut}")
$shortcut.TargetPath = "{install_dir / '进销存管理系统.exe'}"
$shortcut.WorkingDirectory = "{install_dir}"
$shortcut.Description = "进销存管理系统"
$shortcut.Save()
'''
    subprocess.run(['powershell', '-Command', ps_script], capture_output=True)
    
    print()
    print("=" * 50)
    print("     ✓ 安装完成！")
    print("=" * 50)
    print()
    print(f"  安装位置: {install_dir}")
    print(f"  桌面已创建快捷方式")
    print()
    
    # 询问是否运行
    try:
        choice = input("  是否立即运行程序？(Y/N): ").strip().lower()
        if choice == 'y':
            print("  正在启动程序...")
            subprocess.Popen([install_dir / "进销存管理系统.exe"])
    except:
        pass
    
    return True

if __name__ == "__main__":
    install_app()
