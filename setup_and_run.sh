#!/bin/bash

# ======================================================
#    CRACK SERVER - LINUX AUTO LAUNCHER v1.0.0
# ======================================================

echo "======================================================"
echo "   CRACK SERVER 배포 및 실행 스크립트 (Linux)"
echo "======================================================"

# 1. 파이썬 설치 확인
if ! command -v python3 &> /dev/null
then
    echo "[ERROR] python3를 찾을 수 없습니다. 파이썬을 설치해주세요."
    exit 1
fi

# 2. 기존 포트(8012) 종료
echo "[*] 포트 8012 확인 중..."
PID=$(lsof -t -i:8012)
if [ ! -z "$PID" ]; then
    echo "[*] 기존 프로세스($PID) 종료 중..."
    kill -9 $PID
fi

# 3. 가상환경 체크 및 생성
if [ ! -d ".venv" ]; then
    echo "[*] 가상환경 생성 중..."
    python3 -m venv .venv
fi

# 4. 가상환경 활성화 및 라이브러리 설치
source .venv/bin/activate

echo "[*] 라이브러리 설치/업데이트 중 (시간이 소요될 수 있습니다)..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. 서버 실행 (외부 접속 허용을 위해 0.0.0.0 바인딩)
echo "======================================================"
echo "   CRACK SERVER가 http://0.0.0.0:8012 에서 실행됩니다."
echo "======================================================"
export FLASK_RUN_HOST=0.0.0.0
python app.py
