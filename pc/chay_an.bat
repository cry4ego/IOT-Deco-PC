@echo off
REM Chay DeskMon an (khong co cua so). Dat shortcut file nay vao shell:startup de tu chay cung Windows
cd /d "%~dp0"
start "" pythonw deskmon.py --log-file deskmon.log %*
