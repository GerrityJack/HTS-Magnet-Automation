@echo off
TITLE Lab Data Logger (QuestDB)

echo Activating environment...
call "D:\cp311\Scripts\activate.bat"

cd /d "D:\python\AL630"

echo Starting Lab Logger...
python lab_logger.py

echo.
echo Logger stopped.
pause
