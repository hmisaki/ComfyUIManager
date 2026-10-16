@echo off
setlocal
chcp 65001 >nul

rem ===========================================================
rem  ComfyUIManager 启动脚本
rem
rem  用法：
rem    startManager.bat                     使用名为 base 的 conda 环境
rem    set COMFYUI_MANAGER_ENV=myenv & startManager.bat
rem                                        使用指定的 conda 环境
rem
rem  找不到 conda 时会退回 PATH 上的 python。
rem ===========================================================

cd /d "%~dp0"

if "%COMFYUI_MANAGER_ENV%"=="" set COMFYUI_MANAGER_ENV=base

where conda >nul 2>nul
if %ERRORLEVEL%==0 (
    call conda activate "%COMFYUI_MANAGER_ENV%" 2>nul
    if errorlevel 1 echo [!] conda activate %COMFYUI_MANAGER_ENV% 失败，改用 PATH 上的 python。
)

where python >nul 2>nul
if not %ERRORLEVEL%==0 (
    echo [x] 找不到 python。请先安装 Python 3.10+ 或 Miniconda，并勾选加入 PATH。
    pause
    exit /b 1
)

echo [*] 启动 ComfyUIManager ...
python "%~dp0main.py"
if errorlevel 1 pause

endlocal
