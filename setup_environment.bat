@echo off
chcp 65001 >nul
SETLOCAL
TITLE HTS Magnet Testing Automation - Environment Setup
COLOR 0B

echo.
echo  ============================================================
echo   HTS Magnet Testing Automation - Environment Setup
echo  ============================================================
echo.
echo  This script will:
echo    1. Check that Python 3.13 is available
echo    2. Create a virtual environment in the lab folder
echo    3. Install all required packages from requirements.txt
echo    4. Optionally install the LabJack LJM Python bindings
echo.
echo  Prerequisites that must be installed MANUALLY before running:
echo    - Python 3.13    : already installed
echo    - Docker Desktop : https://www.docker.com/products/docker-desktop/
echo    - Mosquitto      : https://mosquitto.org/download/
echo    - LabJack LJM    : https://labjack.com/pages/support
echo.
pause

SET "LAB_DIR=C:\Users\gerri\Desktop\PPPL\Automation System Files"
SET "VENV_DIR=%LAB_DIR%\venv"
SET "REQ_FILE=%LAB_DIR%\requirements.txt"

:: ── Find Python 3.13 ─────────────────────────────────────────────────────────
echo.
echo [1/4] Checking for Python 3.13...

py -3.13 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    FOR /F "tokens=*" %%v IN ('py -3.13 --version') DO echo  Found via py launcher: %%v
    SET "PYTHON_CMD=py -3.13"
    goto PYTHON_FOUND
)

python --version 2>&1 | findstr "3.13" >nul
IF %ERRORLEVEL% EQU 0 (
    FOR /F "tokens=*" %%v IN ('python --version') DO echo  Found via python: %%v
    SET "PYTHON_CMD=python"
    goto PYTHON_FOUND
)

python3 --version 2>&1 | findstr "3.13" >nul
IF %ERRORLEVEL% EQU 0 (
    FOR /F "tokens=*" %%v IN ('python3 --version') DO echo  Found via python3: %%v
    SET "PYTHON_CMD=python3"
    goto PYTHON_FOUND
)

echo  ERROR: Python 3.13 not found on PATH.
echo  Try reopening this terminal as Administrator,
echo  or reinstall Python 3.13 and tick "Add Python to PATH".
goto FATAL

:PYTHON_FOUND
echo.

:: ── Create virtual environment ────────────────────────────────────────────────
echo [2/4] Creating virtual environment...
echo  Location: %VENV_DIR%
echo.

IF EXIST "%VENV_DIR%" (
    echo  A virtual environment already exists.
    choice /C YN /M "Recreate it from scratch?"
    IF %ERRORLEVEL% EQU 1 (
        echo  Removing existing environment...
        rmdir /S /Q "%VENV_DIR%"
    ) ELSE (
        echo  Keeping existing environment.
        goto INSTALL
    )
)

%PYTHON_CMD% -m venv "%VENV_DIR%"
IF %ERRORLEVEL% NEQ 0 (
    echo  ERROR: Failed to create virtual environment.
    goto FATAL
)
echo  OK - virtual environment created.
echo.

:INSTALL
:: ── Activate and upgrade pip ──────────────────────────────────────────────────
echo [3/4] Installing packages...
call "%VENV_DIR%\Scripts\activate.bat"

echo  Upgrading pip...
python -m pip install --upgrade pip --quiet

IF NOT EXIST "%REQ_FILE%" (
    echo  ERROR: requirements.txt not found at:
    echo  %REQ_FILE%
    goto FATAL
)

echo  Installing packages from requirements.txt...
pip install -r "%REQ_FILE%"
IF %ERRORLEVEL% NEQ 0 (
    echo  ERROR: Package installation failed.
    goto FATAL
)
echo  OK - all packages installed.
echo.

:: ── Optional: LabJack LJM bindings ───────────────────────────────────────────
echo [4/4] LabJack LJM Python bindings...
echo.
echo  The LabJack LJM Python package requires the LJM system driver first.
echo.
choice /C YN /M "Is the LabJack LJM driver already installed? Install Python bindings now?"
IF %ERRORLEVEL% EQU 1 (
    pip install labjack-ljm
    python -c "from labjack import ljm" >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        echo  OK - labjack-ljm installed and importable.
    ) ELSE (
        echo  WARNING: labjack-ljm installed but cannot be imported.
        echo  The LJM system driver may not be installed yet.
        echo  Install it from https://labjack.com then rerun this script.
    )
) ELSE (
    echo  Skipping.
)
echo.

:: ── Run verification script ───────────────────────────────────────────────────
IF EXIST "%LAB_DIR%\verify_environment.py" (
    echo  Running verification script...
    python "%LAB_DIR%\verify_environment.py"
) ELSE (
    echo  verify_environment.py not found - skipping verification.
)

echo.
echo  ============================================================
echo   Setup complete.
echo   To activate this environment manually:
echo     call "%VENV_DIR%\Scripts\activate.bat"
echo   To run the lab system:
echo     Double-click startup.bat
echo  ============================================================
echo.
pause
exit /b 0

:FATAL
echo.
echo  ============================================================
echo   SETUP FAILED - see error above.
echo  ============================================================
pause
exit /b 1
