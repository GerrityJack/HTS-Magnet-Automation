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

:: %~dp0 is the folder this script lives in (with trailing backslash)
SET "LAB_DIR=%~dp0"
IF "%LAB_DIR:~-1%"=="\" SET "LAB_DIR=%LAB_DIR:~0,-1%"

SET "VENV_ACTIVATE=%LAB_DIR%\venv\Scripts\activate.bat"
SET "MOSQUITTO_EXE=C:\Users\scuser\MQTT\Mosquitto\mosquitto.exe"
IF NOT EXIST "%MOSQUITTO_EXE%" SET "MOSQUITTO_EXE=C:\Program Files\mosquitto\mosquitto.exe"
SET "MOSQUITTO_SUB=C:\Users\scuser\MQTT\Mosquitto\mosquitto_sub.exe"
IF NOT EXIST "%MOSQUITTO_SUB%" SET "MOSQUITTO_SUB=C:\Program Files\mosquitto\mosquitto_sub.exe"
SET "QUESTDB_REMOTE=198.125.227.226"
SET QUESTDB_PORT=9000
:: Grafana -- update GRAFANA_EXE if installed somewhere other than the
:: standard location, or leave as-is if Grafana runs as a Windows service.
SET "GRAFANA_EXE=D:\Program Files\grafana-v12.0.2\bin\grafana.exe"
SET GRAFANA_PORT=3000
SET GRAFANA_WAIT=10
SET MQTT_HOST=localhost
SET MQTT_PORT=1883
SET MOSQUITTO_WAIT=4
SET DRIVER_WAIT=4
SET HAD_ERROR=0

:: ── Step 1: Check virtual environment ────────────────────────────────────────
echo [1/7] Checking Python environment...
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
echo [2/7] Starting Mosquitto MQTT broker...

:: Check if already listening on port 1883
powershell -NoProfile -Command "try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('localhost',%MQTT_PORT%);$t.Close();exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Mosquitto already running on port %MQTT_PORT%.
    goto MOSQUITTO_DONE
)

:: Try Windows service first
net start mosquitto >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Mosquitto started as Windows service.
    goto MOSQUITTO_DONE
)

:: Launch the exe directly
IF NOT EXIST "%MOSQUITTO_EXE%" (
    echo  ERROR: Mosquitto not found at:
    echo         %MOSQUITTO_EXE%
    echo  Update MOSQUITTO_EXE at the top of this script.
    SET HAD_ERROR=1
    goto SHOW_RESULT
)
start "Mosquitto Broker" /MIN cmd /c ""%MOSQUITTO_EXE%" -v"
echo  Waiting %MOSQUITTO_WAIT%s for Mosquitto to start...
timeout /t %MOSQUITTO_WAIT% /nobreak >nul

:: Verify it came up
powershell -NoProfile -Command "try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('localhost',%MQTT_PORT%);$t.Close();exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Mosquitto running at localhost:%MQTT_PORT%
) ELSE (
    echo  ERROR: Mosquitto launched but not responding on port %MQTT_PORT%.
    echo  Check the Mosquitto Broker window for error messages.
    SET HAD_ERROR=1
    goto SHOW_RESULT
)

:MOSQUITTO_DONE
echo.

:: ── Step 3: Check / Start QuestDB ────────────────────────────────────────────
echo [3/7] Checking QuestDB...

:: QuestDB is already running on this machine separately and does not
:: need to be launched here -- the local launch attempt requires write
:: access to its data folder which this account does not have. Just
:: verify it is reachable so the rest of the script knows whether to
:: warn about it being unavailable.
powershell -NoProfile -Command "try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('localhost',%QUESTDB_PORT%);$t.Close();exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - QuestDB already running at localhost:%QUESTDB_PORT%
) ELSE (
    echo  WARNING: QuestDB not reachable at localhost:%QUESTDB_PORT%.
    echo  Start it manually if needed -- data will not be logged until it is up.
)

:QUESTDB_DONE
echo.

:: -- Step 4: Start Grafana -------------------------------------------------
echo [4/7] Starting Grafana...

:: Check if Grafana is already listening on port 3000
powershell -NoProfile -Command "try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('localhost',%GRAFANA_PORT%);$t.Close();exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Grafana already running at localhost:%GRAFANA_PORT%
    goto GRAFANA_DONE
)

:: Try to start as a Windows service first
net start grafana >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Grafana started as Windows service.
    goto GRAFANA_WAIT
)

:: Fall back to launching the exe directly if a service install is not present
IF EXIST "%GRAFANA_EXE%" (
    for %%G in ("%GRAFANA_EXE%") do SET "GRAFANA_BIN_DIR=%%~dpG"
start "Grafana" /MIN cmd /c "cd /d "%GRAFANA_BIN_DIR%" && "%GRAFANA_EXE%" server"
    echo  Launching Grafana...
    goto GRAFANA_WAIT
) ELSE (
    echo  INFO: Grafana not found as a service or at %GRAFANA_EXE%
    echo  Update GRAFANA_EXE at the top of this script if installed elsewhere.
    echo  Continuing without Grafana -- you can start it manually later.
    goto GRAFANA_DONE
)

:GRAFANA_WAIT
echo  Waiting %GRAFANA_WAIT%s for Grafana to initialise...
timeout /t %GRAFANA_WAIT% /nobreak >nul
powershell -NoProfile -Command "try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('localhost',%GRAFANA_PORT%);$t.Close();exit 0}catch{exit 1}" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo  OK - Grafana ready at localhost:%GRAFANA_PORT%
) ELSE (
    echo  WARNING: Grafana not responding yet -- it may still be starting.
    echo  Check http://localhost:%GRAFANA_PORT% in a browser shortly.
)

:GRAFANA_DONE
echo.

:: ── Step 4: Start LabJack driver ──────────────────────────────────────────────
echo [5/7] Starting LabJack driver...
IF NOT EXIST "%LAB_DIR%\labjack_mqtt_driver.py" (
    echo  WARNING: labjack_mqtt_driver.py not found - skipping.
    goto LABJACK_DONE
)
SET LJ_COUNT=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*labjack_mqtt_driver*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET LJ_COUNT=%%p
IF "!LJ_COUNT!" GTR "0" (
    echo  INFO: LabJack driver already running - skipping.
) ELSE (
    start "LabJack Driver" /MIN cmd /k "chcp 65001 >nul && call "%VENV_ACTIVATE%" && cd /d "%LAB_DIR%" && python labjack_mqtt_driver.py"
    echo  Waiting %DRIVER_WAIT%s for LabJack driver to initialise...
    timeout /t %DRIVER_WAIT% /nobreak >nul
    echo  OK - LabJack driver launched.
)
:LABJACK_DONE
echo.

:: ── Step 5: Start Compressor driver ──────────────────────────────────────────
echo [6/7] Starting Compressor driver...
IF NOT EXIST "%LAB_DIR%\compressor_mqtt_driver.py" (
    echo  WARNING: compressor_mqtt_driver.py not found - skipping.
    goto COMPRESSOR_DONE
)
SET CP_COUNT=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*compressor_mqtt_driver*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET CP_COUNT=%%p
IF "!CP_COUNT!" GTR "0" (
    echo  INFO: Compressor driver already running - skipping.
) ELSE (
    start "Compressor Driver" /MIN cmd /k "chcp 65001 >nul && call "%VENV_ACTIVATE%" && cd /d "%LAB_DIR%" && python compressor_mqtt_driver.py"
    echo  Waiting %DRIVER_WAIT%s for Compressor driver to initialise...
    timeout /t %DRIVER_WAIT% /nobreak >nul
    echo  OK - Compressor driver launched.
)
:COMPRESSOR_DONE
echo.

:: ── Step 6: Start Lab Logger ──────────────────────────────────────────────────
echo [7/7] Starting Lab Logger...
IF NOT EXIST "%LAB_DIR%\lab_logger.py" (
    echo  WARNING: lab_logger.py not found - skipping.
    goto LOGGER_DONE
)
SET LG_COUNT=0
FOR /F %%p IN ('powershell -NoProfile -Command "Get-Process python*,python3* -ErrorAction SilentlyContinue | Where-Object {$_.Path} | ForEach-Object {(Get-WmiObject Win32_Process -Filter (\"ProcessId=\" + $_.Id)).CommandLine} | Where-Object {$_ -like \"*lab_logger*\"} | Measure-Object | Select-Object -ExpandProperty Count" 2^>nul') DO SET LG_COUNT=%%p
IF "!LG_COUNT!" GTR "0" (
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
echo   All background services started.
echo  ============================================================
echo.

:: Open QuestDB and Grafana web UIs automatically so they are ready
:: to view alongside the GUI.
echo  Opening QuestDB console and Grafana dashboard in your browser...
start "" "http://localhost:%QUESTDB_PORT%"
start "" "http://localhost:%GRAFANA_PORT%/d/hts-magnet-automation/hts-magnet-testing-automation"
echo.

echo  ============================================================
echo   Launching GUI...
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
echo  Stopping background processes and closing their windows...

:: Kill the Python scripts first so they shut down cleanly
powershell -NoProfile -Command "Get-WmiObject Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*labjack_mqtt_driver*' -or $_.CommandLine -like '*compressor_mqtt_driver*' -or $_.CommandLine -like '*lab_logger*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul

:: Now close the cmd /k console windows themselves -- killing the python
:: process above does not close the cmd window it was running inside,
:: since /k means "keep the window open". Close by window title instead.
taskkill /FI "WINDOWTITLE eq LabJack Driver*"    /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Compressor Driver*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Lab Logger*"        /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Mosquitto Broker*"  /T /F >nul 2>&1

echo  Done. QuestDB and Grafana left running (data is safe).

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
