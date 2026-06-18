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

:: %~dp0 expands to the folder this script lives in, with a trailing
:: backslash. This makes the script work from anywhere it is placed --
:: no hardcoded path required.
SET "LAB_DIR=%~dp0"
:: Strip the trailing backslash so paths built from LAB_DIR are consistent
:: with the rest of the script (e.g. "%LAB_DIR%\venv" not "%LAB_DIR%\\venv")
IF "%LAB_DIR:~-1%"=="\" SET "LAB_DIR=%LAB_DIR:~0,-1%"
SET "VENV_DIR=%LAB_DIR%\venv"
SET "REQ_FILE=%LAB_DIR%\requirements.txt"

:: ── Find Python 3.13 ─────────────────────────────────────────────────────────
echo.
echo [1/4] Checking for Python 3.13...
echo  ^(Trying each detection method in order -- this may take a moment^)
echo.

:: -- Method 1: python3.13 -- the exact name uv uses on this machine.
:: Tried first since this is confirmed to work via where.exe.
echo   - Trying "python3.13"...
python3.13 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    FOR /F "tokens=*" %%v IN ('python3.13 --version 2^>^&1') DO echo  Found via python3.13: %%v
    SET "PYTHON_CMD=python3.13"
    goto PYTHON_FOUND
)

:: -- Method 2: direct path to the uv install location, in case PATH
:: has not propagated to this shell yet even though the file exists.
echo   - Trying direct uv path...
IF EXIST "%USERPROFILE%\.local\bin\python3.13.exe" (
    "%USERPROFILE%\.local\bin\python3.13.exe" --version >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        echo  Found via direct uv path: %USERPROFILE%\.local\bin\python3.13.exe
        SET "PYTHON_CMD=%USERPROFILE%\.local\bin\python3.13.exe"
        goto PYTHON_FOUND
    )
)

:: -- Method 3: py launcher (python.org installs only -- uv does not
:: register with this, so it is expected to fail on uv-only machines).
echo   - Trying "py -3.13" launcher...
py -3.13 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    FOR /F "tokens=*" %%v IN ('py -3.13 --version 2^>^&1') DO echo  Found via py launcher: %%v
    SET "PYTHON_CMD=py -3.13"
    goto PYTHON_FOUND
)

:: -- Method 4: bare "python" command, only accepted if it reports 3.13
:: exactly (avoids accidentally picking up a 3.14 or other version).
echo   - Trying "python"...
python --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    python --version 2>&1 | findstr /C:"3.13" >nul
    IF %ERRORLEVEL% EQU 0 (
        FOR /F "tokens=*" %%v IN ('python --version 2^>^&1') DO echo  Found via python: %%v
        SET "PYTHON_CMD=python"
        goto PYTHON_FOUND
    ) ELSE (
        echo     "python" exists but is not 3.13 -- skipping.
    )
)

:: -- Method 5: bare "python3" command, same exact-version check.
echo   - Trying "python3"...
python3 --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    python3 --version 2>&1 | findstr /C:"3.13" >nul
    IF %ERRORLEVEL% EQU 0 (
        FOR /F "tokens=*" %%v IN ('python3 --version 2^>^&1') DO echo  Found via python3: %%v
        SET "PYTHON_CMD=python3"
        goto PYTHON_FOUND
    ) ELSE (
        echo     "python3" exists but is not 3.13 -- skipping.
    )
)

echo.
echo  ERROR: Python 3.13 not found by any detection method.
echo  Diagnostic info -- run these manually to check:
echo    where.exe python3.13
echo    python3.13 --version
echo.
echo  If you installed Python with uv, add this folder to PATH:
echo    %USERPROFILE%\.local\bin
echo  ^(Settings ^> Edit environment variables for your account ^> User Path^)
echo  Then close ALL terminal windows and run setup_environment.bat again.
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
