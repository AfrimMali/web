@echo off
REM Signal Publisher - review this morning's proposal and publish it.
REM Double-click this, or make a desktop shortcut to it.
cd /d "%~dp0"
python publish.py
echo.
pause
