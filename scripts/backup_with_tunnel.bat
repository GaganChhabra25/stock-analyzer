@echo off
title DB Backup via SSH Tunnel
color 0A

:: ============================================================
::  EDIT THESE TWO LINES WITH YOUR CONTABO SERVER DETAILS
:: ============================================================
set CONTABO_IP=185.211.6.5
set SSH_USER=root
:: ============================================================

echo ================================================
echo   Stock Analyzer — Database Backup
echo   Server : %SSH_USER%@%CONTABO_IP%
echo   Saving : D:\Gagan\database_backups
echo ================================================
echo.

:: Check if IP is configured
if "%CONTABO_IP%"=="YOUR_CONTABO_IP_HERE" (
    echo  ERROR: CONTABO_IP not set!
    echo  Edit this file and set your Contabo server IP.
    pause
    exit /b 1
)

:: Step 1: Open SSH tunnel on port 5433 (5432 is used by local PostgreSQL)
echo [1/3] Opening SSH tunnel on localhost:5433 ...
start "SSH-Tunnel" /min ssh -L 5433:localhost:5432 %SSH_USER%@%CONTABO_IP% -N -o StrictHostKeyChecking=no -o ServerAliveInterval=30

:: Wait for tunnel to establish
echo       Waiting 5 seconds for tunnel...
timeout /t 5 /nobreak >nul

:: Step 2: Run backup using port 5433
echo [2/3] Running backup via tunnel...
echo.
cd /d D:\Gagan\Projects\stock-analyzer
python scripts\backup_db.py --port 5433

:: Step 3: Close tunnel
echo.
echo [3/3] Closing SSH tunnel...
taskkill /FI "WINDOWTITLE eq SSH-Tunnel" /F >nul 2>&1

echo.
if %ERRORLEVEL% EQU 0 (
    color 0A
    echo  SUCCESS — Backup saved to D:\Gagan\database_backups
) else (
    color 0C
    echo  ERROR — Check output above
)

echo.
pause
