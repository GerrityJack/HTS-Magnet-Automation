@echo off
chcp 65001 >nul
SETLOCAL
TITLE Create Desktop Shortcut

echo.
echo  ============================================================
echo   Creating Desktop Shortcut for HTS Magnet Testing Automation
echo  ============================================================
echo.

:: %~dp0 is the folder this script lives in
SET "LAB_DIR=%~dp0"
IF "%LAB_DIR:~-1%"=="\" SET "LAB_DIR=%LAB_DIR:~0,-1%"

SET "SHORTCUT_PATH=%USERPROFILE%\Desktop\Start HTS Automation.lnk"
SET "TARGET_PATH=%LAB_DIR%\startup.bat"

IF NOT EXIST "%TARGET_PATH%" (
    echo  ERROR: startup.bat not found at:
    echo         %TARGET_PATH%
    echo  Run this script from the same folder as startup.bat.
    pause
    exit /b 1
)

:: If a shortcut already exists, delete it first so the new one
:: always reflects the current TARGET_PATH and settings below.
IF EXIST "%SHORTCUT_PATH%" (
    echo  Existing shortcut found - replacing it...
    del /F "%SHORTCUT_PATH%" >nul 2>&1
)

:: Use PowerShell to create the .lnk file since batch cannot do this directly
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$sc = $ws.CreateShortcut('%SHORTCUT_PATH%'); " ^
    "$sc.TargetPath = '%TARGET_PATH%'; " ^
    "$sc.WorkingDirectory = '%LAB_DIR%'; " ^
    "$sc.IconLocation = 'cmd.exe,0'; " ^
    "$sc.Description = 'Start HTS Magnet Testing Automation'; " ^
    "$sc.Save()"

IF %ERRORLEVEL% NEQ 0 (
    echo  ERROR: Failed to create shortcut.
    pause
    exit /b 1
)

echo  OK - Shortcut created (or replaced) at:
echo       %SHORTCUT_PATH%
echo.
echo  You can now double-click "Start HTS Automation" on your Desktop
echo  to launch the entire system.
echo.
pause
