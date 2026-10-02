@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONHOME=
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -I -S start.py
  goto end
)
where python >nul 2>&1
if not errorlevel 1 (
  python -I -S start.py
  goto end
)
echo 没有找到 Python 3.10 或更新版本。程序不需要 pip 安装依赖。
pause
exit /b 1
:end
if errorlevel 1 pause
