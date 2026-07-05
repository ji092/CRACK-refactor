@echo off
chcp 65001 >nul
setlocal
echo ======================================================
echo    CRACK SERVER - PORTABLE LAUNCHER v1.2.8
echo ======================================================
echo.

:: 0. Check extraction
if not exist "%~dp0app.py" (
    echo [ERROR] Please extract the ZIP file first.
    pause
    exit /b
)

:: 1. Set python executable
set "PYTHON_EXE=python"
if exist "%~dp0python_portable\python.exe" (
    echo [*] Using local portable python...
    set "PYTHON_EXE=%~dp0python_portable\python.exe"
) else (
    echo [*] Checking system python...
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found. Please install Python 3.10+ and try again.
        pause
        exit /b
    )
)

:: 2. Kill existing process on port 8012
echo [*] Checking port 8012...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8012') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: 3. Check and create virtual environment
if not exist "%~dp0.venv" (
    echo [*] Creating virtual environment...
    "%PYTHON_EXE%" -m venv "%~dp0.venv"
)

:: 4. Install required libraries (only if changed)
set "REQ_FILE=%~dp0requirements.txt"
set "INSTALLED_FLAG=%~dp0.venv\installed_requirements.txt"

echo [*] Checking libraries...
set "NEED_INSTALL=no"
if not exist "%INSTALLED_FLAG%" (
    set "NEED_INSTALL=yes"
) else (
    fc "%REQ_FILE%" "%INSTALLED_FLAG%" >nul 2>&1
    if errorlevel 1 set "NEED_INSTALL=yes"
)

if "%NEED_INSTALL%"=="yes" (
    echo [*] Installing/Updating libraries ^(this may take a few minutes^)...
    call "%~dp0.venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul 2>&1
    pip install -r "%REQ_FILE%"
    copy /y "%REQ_FILE%" "%INSTALLED_FLAG%" >nul
) else (
    echo [*] All libraries are up to date. Skipping install.
    call "%~dp0.venv\Scripts\activate.bat"
)

:: 5. Run server
cls
echo ======================================================
echo    CRACK SERVER is running on http://localhost:8012
echo ======================================================
echo.
python "%~dp0app.py"

echo.
echo Server stopped.
pause
