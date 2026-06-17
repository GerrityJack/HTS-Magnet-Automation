@echo off
chcp 65001 >nul
SETLOCAL ENABLEDELAYEDEXPANSION
TITLE HTS Magnet Testing Automation - System Startup
COLOR 0A

echo.
echo  ============================================================
echo   HTS Magnet Testing Automation - System Startup
echo   %DATE%  %TIME%
echo  ============================================================
echo.

SET "LAB_DIR=C:\Users\gerri\Desktop\PPPL\Automation System Files"
SET "VENV_ACTIVATE=%LAB_DIR%\venv\Scripts\activate.bat"
SET "MOSQUITTO_EXE=C:\Program Files\mosquitto\mosquitto.exe"
SET "MOSQUITTO_SUB=C:\Program Files\mosquitto\mosquitto_sub.exe"
SET QUESTDB_CONTAINER=questdb
SET MQTT_PORT=1883
SET QUESTDB_PORT=9000
SET MOSQUITTO_WAIT=3
SET QUESTDB_WAIT=8
SET DRIVER_WAIT=4
SET HAD_ERROR=0

:: ── Step 1: Check virtual environment ────────────────────────────────────────
echo [1/6] Checking Python environment...
IF NOT EXIST "%VENV_ACTIVATE%" (
    echo.
    echo  ERROR: Virtual environment not found at:
    echo         %VENV_ACTIVATE%
    echo  Run setup_environment.bat first.
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
call "%VENV_ACTIVATE%"
IF %ERRORLEVEL% NEQ 0 (
    echo  ERROR: Failed to activate virtual environment.
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
echo  OK - environment activated.
echo.

:: ── Step 2: Start Mosquitto ───────────────────────────────────────────────────
echo [2/6] Starting Mosquitto MQTT broker...
sc query mosquitto >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    sc query mosquitto | findstr "RUNNING" >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        echo  OK - Mosquitto already running as Windows service.
        goto MOSQUITTO_DONE
    )
)
net start mosquitto >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Mosquitto started as Windows service.
    goto MOSQUITTO_DONE
)
IF NOT EXIST "%MOSQUITTO_EXE%" (
    echo  ERROR: Mosquitto not found at %MOSQUITTO_EXE%
    echo  Install from https://mosquitto.org/download/
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
start "Mosquitto Broker" /MIN "%MOSQUITTO_EXE%"
echo  Waiting %MOSQUITTO_WAIT%s for broker to start...
timeout /t %MOSQUITTO_WAIT% /nobreak >nul
:MOSQUITTO_DONE
IF EXIST "%MOSQUITTO_SUB%" (
    "%MOSQUITTO_SUB%" -h localhost -p %MQTT_PORT% -t "test" -C 0 -W 2 >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        echo  OK - MQTT broker verified.
    ) ELSE (
        echo  WARNING: Could not verify MQTT broker.
    )
) ELSE (
    echo  INFO: Skipping broker ping.
)
echo.

:: ── Step 3: Start QuestDB ─────────────────────────────────────────────────────
echo [3/6] Starting QuestDB...
FOR /F %%i IN ('docker inspect --format={{.State.Running}} %QUESTDB_CONTAINER% 2^>nul') DO SET QDB_RUNNING=%%i
IF "!QDB_RUNNING!"=="true" (
    echo  OK - QuestDB already running.
    goto QUESTDB_DONE
)
docker inspect %QUESTDB_CONTAINER% >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  Restarting stopped QuestDB container...
    docker start %QUESTDB_CONTAINER% >nul
) ELSE (
    echo  Creating QuestDB container...
    docker run -d --name=%QUESTDB_CONTAINER% -p 9000:9000 -p 8812:8812 -v questdb-data:/root/.questdb questdb/questdb >nul
)
IF %ERRORLEVEL% NEQ 0 (
    echo  ERROR: Could not start QuestDB. Is Docker Desktop running?
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
echo  Waiting %QUESTDB_WAIT%s for QuestDB to initialise...
timeout /t %QUESTDB_WAIT% /nobreak >nul
:QUESTDB_DONE
powershell -Command "try{Invoke-WebRequest -Uri 'http://localhost:%QUESTDB_PORT%' -TimeoutSec 3 -UseBasicParsing|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - QuestDB up at http://localhost:%QUESTDB_PORT%
) ELSE (
    echo  WARNING: QuestDB not responding yet - still initialising.
)
echo.

:: ── Step 4: Start LabJack driver ──────────────────────────────────────────────
echo [4/6] Starting LabJack driver...
IF NOT EXIST "%LAB_DIR%\labjack_mqtt_driver.py" (
    echo  WARNING: labjack_mqtt_driver.py not found - skipping.
    goto LABJACK_DONE
)
SET LJ_RUNNING=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*labjack_mqtt_driver*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET LJ_COUNT=%%p
IF "!LJ_COUNT!" GTR "0" SET LJ_RUNNING=1
IF %LJ_RUNNING% EQU 1 (
    echo  INFO: LabJack driver already running - skipping.
) ELSE (
    start "LabJack Driver" /MIN cmd /k "chcp 65001 >nul && call "%VENV_ACTIVATE%" && cd /d "%LAB_DIR%" && python labjack_mqtt_driver.py"
    echo  Waiting %DRIVER_WAIT%s for LabJack driver...
    timeout /t %DRIVER_WAIT% /nobreak >nul
    echo  OK - LabJack driver launched.
)
:LABJACK_DONE
echo.

:: ── Step 5: Start Compressor driver ──────────────────────────────────────────
echo [5/6] Starting Compressor driver...
IF NOT EXIST "%LAB_DIR%\compressor_mqtt_driver.py" (
    echo  WARNING: compressor_mqtt_driver.py not found - skipping.
    goto COMPRESSOR_DONE
)
SET CP_RUNNING=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*compressor_mqtt_driver*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET CP_COUNT=%%p
IF "!CP_COUNT!" GTR "0" SET CP_RUNNING=1
IF %CP_RUNNING% EQU 1 (
    echo  INFO: Compressor driver already running - skipping.
) ELSE (
    start "Compressor Driver" /MIN cmd /k "chcp 65001 >nul && call "%VENV_ACTIVATE%" && cd /d "%LAB_DIR%" && python compressor_mqtt_driver.py"
    echo  Waiting %DRIVER_WAIT%s for Compressor driver...
    timeout /t %DRIVER_WAIT% /nobreak >nul
    echo  OK - Compressor driver launched.
)
:COMPRESSOR_DONE
echo.

:: ── Step 6: Start Lab Logger ──────────────────────────────────────────────────
echo [6/6] Starting Lab Logger...
IF NOT EXIST "%LAB_DIR%\lab_logger.py" (
    echo  WARNING: lab_logger.py not found - skipping.
    goto LOGGER_DONE
)
SET LG_RUNNING=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*lab_logger*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET LG_COUNT=%%p
IF "!LG_COUNT!" GTR "0" SET LG_RUNNING=1
IF %LG_RUNNING% EQU 1 (
    echo  INFO: Lab Logger already running - skipping.
) ELSE (
    start "Lab Logger" /MIN cmd /k "chcp 65001 >nul && call "%VENV_ACTIVATE%" && cd /d "%LAB_DIR%" && python lab_logger.py"
    timeout /t 2 /nobreak >nul
    echo  OK - Logger launched.
)
:LOGGER_DONE
echo.

:: ── Launch GUI ────────────────────────────────────────────────────────────────
echo  ============================================================
echo   All background services started. Launching GUI...
echo   (This window stays open behind the GUI)
echo  ============================================================
echo.
IF NOT EXIST "%LAB_DIR%\lab_gui.py" (
    echo  ERROR: lab_gui.py not found.
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
cd /d "%LAB_DIR%"
python lab_gui.py

:: ── After GUI closes ──────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   GUI closed.
echo  ============================================================
echo.
choice /C YN /M "Shut down all background driver windows too?"
IF %ERRORLEVEL% EQU 1 goto SHUTDOWN
echo  Background drivers left running.
goto SHOW_RESULT

:SHUTDOWN
echo  Stopping background Python scripts...
powershell -NoProfile -Command "Get-WmiObject Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*labjack_mqtt_driver*' -or $_.CommandLine -like '*compressor_mqtt_driver*' -or $_.CommandLine -like '*lab_logger*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
echo  Done.
echo  QuestDB left running (data is safe).
echo  To stop QuestDB: docker stop questdb

:SHOW_RESULT
echo.
IF %HAD_ERROR% EQU 1 (
    echo  ============================================================
    echo   STARTUP FAILED - read the error above before closing.
    echo  ============================================================
) ELSE (
    echo  ============================================================
    echo   Done.
    echo  ============================================================
)
echo.
pause
