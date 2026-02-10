@echo off
setlocal
set ENV=development

python install/main.py --check
if %ERRORLEVEL% EQU 0 (
    echo [OK] Integrity verified. Launching Nebula Core...
    python -m nebula_core
    goto :end
)

echo [!] Setup required. Initializing background core...

start "Nebula_Core_Service" /min python -m nebula_core

echo [!] Waiting for API to initialize...
:wait_loop
timeout /t 2 >nul
netstat -ano | findstr :8000 >nul
if %ERRORLEVEL% NEQ 0 (
    echo [WAITING] Core is warming up...
    goto :wait_loop
)

echo [OK] Core is ONLINE. Starting Installer...
echo.

python install/main.py

echo.
echo [FINISH] Setup process completed. 
echo [SYSTEM] Please close the minimized 'Nebula_Core_Service' window and restart this bat.
pause

:end