# ---- builder ----
FROM python:3.11-slim AS builder
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# GPU(CUDA 12.1) torch/torchvision 먼저 설치 — RTX A4000(Ampere, sm_86) 추론용.
# CUDA 런타임 라이브러리가 wheel에 번들되므로 별도 nvidia/cuda 베이스 이미지 없이 slim 위에서 동작.
# (실행 시 호스트 NVIDIA 드라이버 + NVIDIA Container Toolkit 필요 — README/compose 참고)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu121

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ultralytics가 의존성으로 GUI용 opencv-python(libxcb 등 X11 요구)을 끌어오므로,
# 제거하고 headless 버전만 남겨 서버 환경에서 cv2 import 실패(libxcb.so.1)를 방지한다.
RUN pip uninstall -y opencv-python opencv-contrib-python 2>/dev/null; \
    pip install --no-cache-dir --force-reinstall opencv-python-headless==4.13.0.92

# ---- runtime ----
FROM python:3.11-slim
WORKDIR /app

# opencv-headless(libglib) + torch(libgomp) 런타임 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

EXPOSE 8012
# 프로덕션 서버: gunicorn + eventlet 워커 (Flask-SocketIO async_mode='eventlet'와 일치).
# Socket.IO는 세션 어피니티가 필요하므로 워커는 1개(-w 1) — 멀티워커는 메시지 큐(Redis 등) 필요.
# (개발 서버 python app.py / werkzeug는 로컬 전용 — app.py __main__ 참고)
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", "0.0.0.0:8012", "app:app"]
