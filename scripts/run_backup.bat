@echo off
title DB Backup — Stock Analyzer
color 0A

echo ================================================
echo   Stock Analyzer — Database Backup
echo   Destination: D:\Gagan\database_backups
echo ================================================
echo.

cd /d D:\Gagan\Projects\stock-analyzer
python scripts\backup_db.py

echo.
if %ERRORLEVEL% EQU 0 (
    echo  SUCCESS — Backup complete!
    echo  Folder: D:\Gagan\database_backups
) else (
    echo  ERROR — Some backups failed. Check output above.
    color 0C
)

echo.
pause
