@echo off
title DB Backup — Stock Analyzer
color 0A
cd /d D:\Gagan\Projects\stock-analyzer
python scripts\backup_db.py
echo.
if %ERRORLEVEL% EQU 0 ( echo  Done! Check D:\Gagan\database_backups ) else ( color 0C && echo  FAILED — see error above )
echo.
pause
