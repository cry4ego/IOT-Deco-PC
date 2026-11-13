@echo off
REM Chay man hinh phu an (khong co cua so). Log ghi ra man_phu.log.
REM Tu chay cung Windows: tao shortcut file nay trong shell:startup (va bo shortcut DeskMon cu, 2 cai khong chay cung luc duoc).
cd /d "%~dp0"
start "" pythonw man_phu.py --log-file man_phu.log %*
