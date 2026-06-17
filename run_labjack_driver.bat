@echo off
TITLE LabJack T7-Pro MQTT Driver

:: Step 1: Activate the Python Environment
echo Activating environment...
call "D:\cp311\Scripts\activate.bat"

:: Step 2: Navigate to the script folder (adjust path as needed)
cd /d "D:\python\AL630"

:: Step 3: Run the LabJack driver
echo Starting LabJack MQTT Driver...
python labjack_mqtt_driver.py

:: Step 4: Keep window open if the script finishes or crashes
echo.
echo Script execution finished.
pause
