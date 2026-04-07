@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
if not exist "instance" mkdir "instance"

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
if "%XAE_ENV%"=="" set "XAE_ENV=development"
set "XAE_SECRET_KEY_GENERATED="
set "XAE_ADMIN_PASSWORD_GENERATED="
set "BOOTSTRAP_FILE=instance\bootstrap-admin-password.txt"
if "%XAE_ADMIN_USERNAME%"=="" set "XAE_ADMIN_USERNAME=admin"
if "%XAE_ADMIN_EMAIL%"=="" set "XAE_ADMIN_EMAIL=admin@localhost"
if "%XAE_AUTO_CREATE_SCHEMA%"=="" set "XAE_AUTO_CREATE_SCHEMA=false"
if "%XAE_HOST%"=="" set "XAE_HOST=127.0.0.1"
set "XAE_BROWSER_HOST=%XAE_HOST%"
if "%XAE_BROWSER_HOST%"=="0.0.0.0" set "XAE_BROWSER_HOST=127.0.0.1"
if "%XAE_BROWSER_HOST%"=="::" set "XAE_BROWSER_HOST=127.0.0.1"
call :resolve_current_user_sid
if "%CURRENT_USER_SID%"=="" (
  echo Failed to resolve current user SID.
  goto :fail
)

if "%XAE_SECRET_KEY%"=="" (
  if /I not "%XAE_ENV%"=="development" (
    echo XAE_SECRET_KEY is required when XAE_ENV is not development.
    goto :fail
  )
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$bytes = New-Object byte[] 48; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); [Convert]::ToBase64String($bytes)"`) do set "XAE_SECRET_KEY=%%A"
  if "%XAE_SECRET_KEY%"=="" (
    echo Failed to generate a secure secret key.
    goto :fail
  )
  set "XAE_SECRET_KEY_GENERATED=1"
)

if "%XAE_ADMIN_PASSWORD%"=="" (
  if /I not "%XAE_ENV%"=="development" (
    echo XAE_ADMIN_PASSWORD is required when XAE_ENV is not development.
    goto :fail
  )
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$bytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); [Convert]::ToBase64String($bytes)"`) do set "XAE_ADMIN_PASSWORD=%%A"
  if "%XAE_ADMIN_PASSWORD%"=="" (
    echo Failed to generate a random admin password.
    goto :fail
  )
  set "XAE_ADMIN_PASSWORD_GENERATED=1"
  >"%BOOTSTRAP_FILE%" (
    echo X Archive Explorer Bootstrap Credentials
    echo Username=%XAE_ADMIN_USERNAME%
    echo Email=%XAE_ADMIN_EMAIL%
    echo Password=%XAE_ADMIN_PASSWORD%
    echo GeneratedAt=%DATE% %TIME%
  )
  icacls "%BOOTSTRAP_FILE%" /inheritance:r /grant:r "*%CURRENT_USER_SID%:(R,W)" >nul
  if errorlevel 1 (
    echo Failed to apply restrictive permissions to %BOOTSTRAP_FILE%.
    del /q "%BOOTSTRAP_FILE%" >nul 2>&1
    goto :fail
  )
)

call :find_free_port
if "%XAE_PORT%"=="" (
  echo No available port was found between 5000 and 5100.
  goto :fail
)
call :validate_runtime_values

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
echo   Password: ^<hidden^>
if defined XAE_SECRET_KEY_GENERATED (
  echo   Secret key was generated automatically for this development run.
)
if defined XAE_ADMIN_PASSWORD_GENERATED (
  echo   Admin password was generated automatically and saved to:
  echo   %BOOTSTRAP_FILE%
)
echo If login fails on an existing database, run:
echo   flask --app run.py reset-user-password --identity %XAE_ADMIN_EMAIL% --password ^<new-password^>
echo.

echo [6/7] Opening browser...
set "APP_URL=http://%XAE_BROWSER_HOST%:%XAE_PORT%/"
start "" powershell -NoProfile -Command "param([string]$u) Start-Sleep -Seconds 2; Start-Process $u" -args "%APP_URL%"

echo [7/7] Starting X Archive Explorer on port %XAE_PORT% with Waitress...
echo URL: %APP_URL%
"%PY%" -m waitress "--listen=%XAE_HOST%:%XAE_PORT%" run:app
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

:resolve_current_user_sid
set "CURRENT_USER_SID="
for /f "usebackq tokens=2 delims=," %%S in (`whoami /user /fo csv /nh`) do set "CURRENT_USER_SID=%%~S"
exit /b 0

:validate_runtime_values
call :validate_hostname_env XAE_HOST
if errorlevel 1 (
  echo Invalid XAE_HOST value: %XAE_HOST%
  echo Please set XAE_HOST to a valid IP address or hostname.
  goto :fail
)

call :validate_hostname_env XAE_BROWSER_HOST
if errorlevel 1 (
  echo Invalid XAE_BROWSER_HOST value: %XAE_BROWSER_HOST%
  echo Please set XAE_BROWSER_HOST to a valid IP address or hostname.
  goto :fail
)

set "XAE_PORT_VALID="
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$p=0; $ok=[int]::TryParse($env:XAE_PORT,[ref]$p) -and $p -ge 1 -and $p -le 65535; if($ok){'1'}else{'0'}"`) do set "XAE_PORT_VALID=%%V"
if not "%XAE_PORT_VALID%"=="1" (
  echo Invalid XAE_PORT value: %XAE_PORT%
  echo Please set XAE_PORT to an integer between 1 and 65535.
  goto :fail
)
exit /b 0

:validate_hostname_env
set "VALIDATE_ENV_NAME=%~1"
call set "VALIDATE_ENV_VALUE=%%%VALIDATE_ENV_NAME%%%"
set "HOST_VALID=0"
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$h=[string]$args[0]; $ok=$false; $ip=[System.Net.IPAddress]::None; if([System.Net.IPAddress]::TryParse($h,[ref]$ip)){ $ok=$true } elseif($h -eq 'localhost'){ $ok=$true } elseif($h -match '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'){ $ok=$true }; if($ok){'1'}else{'0'}" -- "%VALIDATE_ENV_VALUE%"`) do set "HOST_VALID=%%V"
if "%HOST_VALID%"=="1" exit /b 0
exit /b 1

:fail
echo Startup failed.
pause
exit /b 1
