@echo off
REM Chay DeskMon co cua so log (de kiem tra loi)
cd /d "%~dp0"
python deskmon.py %*
pause
