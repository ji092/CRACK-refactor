@echo off
chcp 65001 >nul
echo =========================================
echo 플라스크 서버 시작 스크립트 (포트: 8012)
echo =========================================
echo.

echo 1. 기존에 돌고 있는 서버가 있다면 종료합니다...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8012') do taskkill /F /PID %%a 2>nul
timeout /t 1 /nobreak >nul
echo.

echo 2. 가상 환경(.venv)을 활성화하고 서버를 켭니다...
call .venv\Scripts\activate.bat
python app.py

echo.
echo 서버가 종료되었습니다.
pause
