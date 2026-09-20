@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  以撒 Mod 管理器 启动器 (智能选择运行方式)
rem    1. 优先用打包好的 exe (自动检测 三种位置)
rem    2. 没有 exe 就退回用 Python 源码运行
rem  注意: 程序自带单实例保护, 已在运行时会直接复用, 不会重复启动
rem ============================================================

if exist "IsaacModManager.exe" (
  echo [启动] 使用 exe ...
  start "" "IsaacModManager.exe"
  exit /b 0
)
if exist "dist\IsaacModManager.exe" (
  echo [启动] 使用 dist\IsaacModManager.exe ...
  start "" "dist\IsaacModManager.exe"
  exit /b 0
)
if exist "dist\IsaacModManager-portable\IsaacModManager-portable.exe" (
  echo [启动] 使用便携版 ...
  start "" "dist\IsaacModManager-portable\IsaacModManager-portable.exe"
  exit /b 0
)

echo [启动] 未找到 exe, 改用 Python 源码运行 ...
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没找到 Python。请二选一:
  echo   1) 安装 Python 3 并勾选 "Add to PATH"
  echo   2) 先运行  python build.py  打包出 exe 再双击
  echo.
  pause
  exit /b 1
)
python isaac_mod_manager.py
if errorlevel 1 (
  echo.
  echo 启动失败, 请查看上方错误信息。
  pause
)
