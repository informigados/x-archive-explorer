@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo =============================================================
echo                  X ARCHIVE EXPLORER
echo       Local-first intelligence for X account archives
echo =============================================================
echo.

echo [1/7] Preparing virtual environment...
where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher "py" was not found. Please install Python 3.11+ and try again.
  goto :fail
)

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :fail
)

set "PY=.venv\Scripts\python.exe"
set "FLASK=.venv\Scripts\flask.exe"

echo [2/7] Installing dependencies...
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo [3/7] Resolving runtime configuration...
if "%XAE_SECRET_KEY%"=="" set "XAE_SECRET_KEY=dev-change-me"
if "%XAE_ADMIN_USERNAME%"=="" set "XAE_ADMIN_USERNAME=admin"
if "%XAE_ADMIN_EMAIL%"=="" set "XAE_ADMIN_EMAIL=admin@localhost"
if "%XAE_ADMIN_PASSWORD%"=="" set "XAE_ADMIN_PASSWORD=change-this-password"
if "%XAE_AUTO_CREATE_SCHEMA%"=="" set "XAE_AUTO_CREATE_SCHEMA=false"
if "%XAE_ENV%"=="" set "XAE_ENV=development"
if "%XAE_HOST%"=="" set "XAE_HOST=127.0.0.1"
set "XAE_BROWSER_HOST=%XAE_HOST%"
if "%XAE_BROWSER_HOST%"=="0.0.0.0" set "XAE_BROWSER_HOST=127.0.0.1"
if "%XAE_BROWSER_HOST%"=="::" set "XAE_BROWSER_HOST=127.0.0.1"

call :find_free_port
if "%XAE_PORT%"=="" (
  echo No available port was found between 5000 and 5100.
  goto :fail
)

echo [4/7] Reconciling migration state...
set "DB_MIGRATION_STATE=NO_STAMP"
for /f "usebackq delims=" %%S in (`"%PY%" scripts\detect_legacy_db.py`) do set "DB_MIGRATION_STATE=%%S"
if /i "%DB_MIGRATION_STATE%"=="STAMP_HEAD" (
  echo Legacy database schema detected without Alembic history. Stamping current revision...
  "%FLASK%" --app run.py db stamp head
  if errorlevel 1 goto :fail
)

echo [5/7] Applying database migrations...
"%FLASK%" --app run.py db upgrade
if errorlevel 1 goto :fail

echo.
echo Default local access:
echo   Username: %XAE_ADMIN_USERNAME%
echo   Email:    %XAE_ADMIN_EMAIL%
echo   Password: %XAE_ADMIN_PASSWORD%
echo If login fails on an existing database, run:
echo   flask --app run.py reset-user-password --identity %XAE_ADMIN_EMAIL% --password %XAE_ADMIN_PASSWORD%
echo.

echo [6/7] Opening browser...
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://%XAE_BROWSER_HOST%:%XAE_PORT%/'"

echo [7/7] Starting X Archive Explorer on port %XAE_PORT% with Waitress...
echo URL: http://%XAE_BROWSER_HOST%:%XAE_PORT%/
"%PY%" -m waitress --listen=%XAE_HOST%:%XAE_PORT% run:app
goto :eof

:find_free_port
set "XAE_PORT="
for /f "usebackq delims=" %%P in (`
  powershell -NoProfile -Command ^
    "$start=5000; ^
     $end=5100; ^
     $port=$null; ^
     for($p=$start;$p -le $end;$p++){ ^
       try { ^
         $l=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$p); ^
         $l.Start(); ^
         $l.Stop(); ^
         $port=$p; ^
         break ^
       } catch {} ^
     }; ^
     if($port){Write-Output $port}"`) do set "XAE_PORT=%%P"
exit /b 0

:fail
echo Startup failed.
pause
exit /b 1
