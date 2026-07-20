# [필수] eventlet monkey_patch를 다른 모듈 import보다 먼저 실행한다.
# 이래야 threading.Thread(예: 비동기 AI 분석)가 green thread가 되어,
# 스레드 내부의 socketio.emit(관리자 실시간 토스트 등)이 eventlet 이벤트 루프로 정상 전달된다.
# (gunicorn eventlet 워커는 자동 패치하지만, python app.py 개발 서버는 이게 없으면 emit 누락)
import eventlet
eventlet.monkey_patch()

import os
import certifi
from flask import Flask, render_template, session, redirect, url_for, send_from_directory, make_response
from flask_socketio import join_room
from dotenv import load_dotenv
from ultralytics import YOLO

# 내부 모듈 임포트
from extensions import db, socketio
from models import Report, Member
from core import ai_core

# 서비스 Blueprint 임포트
from services.auth_service import auth_bp
from services.alert_service import alert_bp
from services.report_service import report_bp
from services.status_service import status_bp
from services.my_service import my_bp
from services.admin_service import admin_bp

# .env 파일 로드 (secrets 폴더 확인)
base_dir = os.path.dirname(__file__)
env_path = os.path.join(base_dir, 'secrets', '.env')

if not os.path.exists(env_path):
    print("\n" + "!"*50)
    print("⚠️  CRITICAL ERROR: 'secrets/.env' FILE NOT FOUND!")
    print("'secrets' 폴더를 생성하고 DB 접속 정보 등 필요한 설정값을 '.env' 파일에 작성해야 합니다.")
    print("!"*50 + "\n")
else:
    load_dotenv(env_path)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY가 설정되지 않았습니다. secrets/.env에 FLASK_SECRET_KEY를 추가하세요.")

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

# DB 설정 (TiDB Cloud 연결 지원)
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_port = os.getenv('DB_PORT', '3306')
db_name = os.getenv('DB_NAME')

if not all([db_user, db_password, db_host, db_name]):
    print("⚠️  Warning: Database environment variables are missing.")
    # 기본값 설정을 통해 최소한의 구성은 유지하거나 에러 처리 필요
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///temp_debug.db'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?ssl_ca={certifi.where()}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True, 
    'pool_recycle': 3600,
    'connect_args': {
        'init_command': "SET time_zone = '+09:00'"
    }
}

# 업로드 설정 (최대 100MB)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
UPLOAD_BASE_DIR = os.path.join(base_dir, 'uploads')
UPLOAD_IMAGE_DIR = os.path.join(UPLOAD_BASE_DIR, 'images')
UPLOAD_VIDEO_DIR = os.path.join(UPLOAD_BASE_DIR, 'videos')

# 디렉토리 생성
for d in [UPLOAD_IMAGE_DIR, UPLOAD_VIDEO_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# DB 초기화
db.init_app(app)
socketio.init_app(app)


# [실시간 알림] 관리자 전용 룸(admins) 관리.
# 소켓 연결 시 세션이 관리자인 클라이언트만 'admins' 룸에 join시켜,
# AI 유효 판정 토스트(new_valid_report)를 관리자에게만 room 단위로 emit한다.
# (기존 전체 브로드캐스트 대비, 페이로드가 일반 사용자 소켓으로 전달되지 않음)
@socketio.on('connect')
def _on_socket_connect():
    if session.get('is_admin'):
        join_room('admins')


# 추론 디바이스 결정 (GPU 있으면 cuda, 없으면 cpu로 자동 폴백)
try:
    import torch
    AI_DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
    if AI_DEVICE == 'cuda':
        print(f"[AI] CUDA 사용 가능 — GPU 추론: {torch.cuda.get_device_name(0)}")
    else:
        print("[AI] CUDA 미탐지 — CPU 추론으로 동작합니다.")
except Exception as e:
    AI_DEVICE = 'cpu'
    print(f"[AI] torch 로드 경고: {e} — CPU 추론으로 동작합니다.")

# AI 모델 로드
try:
    model_path = os.path.join(base_dir, 'static', 'best_merge_v2.pt')
    model = YOLO(model_path)
    model.to(AI_DEVICE)  # GPU 상주 (추론 시 매번 옮기지 않도록)
except Exception as e:
    print(f"Error loading YOLO model: {e}")
    model = None

# 배수구/우수관 모델 (파일이 존재할 때만 로드 — 없으면 배수구 제보는 관리자 수동 검토로 전환됨)
drain_model = None
drain_model_path = os.path.join(base_dir, 'static', 'drain_v1.pt')
if os.path.exists(drain_model_path):
    try:
        drain_model = YOLO(drain_model_path)
        drain_model.to(AI_DEVICE)
        print(f"[AI] Drain model loaded: drain_v1.pt (device={AI_DEVICE})")
    except Exception as e:
        print(f"Error loading drain model: {e}")
else:
    print("[AI] Drain model not found (static/drain_v1.pt) — 배수구 제보는 수동 검토로 처리됩니다.")

# Blueprint 등록
app.register_blueprint(auth_bp)
app.register_blueprint(alert_bp)
app.register_blueprint(report_bp)
app.register_blueprint(status_bp)
app.register_blueprint(my_bp)
app.register_blueprint(admin_bp)

# --- 공통 기능 및 API 설정 --- #

# 카카오 JS 키 로드 및 주입
kakao_js_key = ""
try:
    with open(os.path.join(base_dir, 'secrets', 'kakao_js_key.txt'), 'r', encoding='utf-8') as f:
        kakao_js_key = f.read().strip()
    app.config['KAKAO_JS_KEY'] = kakao_js_key
except Exception as e:
    print(f"Error loading kakao js key: {e}")

# --- Moved to services/admin_service.py ---

@app.context_processor
def inject_global_vars():
    """모든 템플릿에서 쓸 수 있는 전역 변수 주입"""
    admin_unread_count = 0
    if session.get('is_admin'):
        admin_unread_count = Report.query.filter_by(status='관리자 확인중').count()
    return dict(kakao_js_key=kakao_js_key, admin_unread_count=admin_unread_count)

# 정적 파일 서빙
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def serve_sw():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    return response

@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(UPLOAD_BASE_DIR, filename)

# 메인 및 공통 라우트
@app.route('/')
def index():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    
    # [보정] 세션 어드민 권한 동기화 (DB 상태와 세션 불일치 해결)
    user = Member.query.get(session['user_id'])
    if user:
        session['is_admin'] = user.is_admin
        
    return render_template('index.html')

@app.route('/login_page')
def login_page():
    return redirect(url_for('auth.login'))

@app.route('/map-test')
def map_test():
    return render_template('map_test.html')

# AI 분석기 등록 (Flask extension 패턴): 부팅 시 로드한 모델을 앱 스코프에 보유.
# 서비스는 current_app.extensions['ai'].analyze(report_id, file_path, file_type, category)로 호출한다.
# 실제 추론 로직은 core/ai_core.py에 있고, core는 Flask에 의존하지 않는다.
app.extensions['ai'] = ai_core.AIAnalyzer(app, {'road': model, 'drain': drain_model}, base_dir)

# 서버 실행부
# 테이블 생성: 모듈 로드 시 1회 실행.
# gunicorn 등 WSGI 서버로 띄우면 `if __name__ == '__main__'` 블록이 실행되지 않으므로,
# 새 환경에서도 테이블이 생성되도록 __main__ 밖에 둔다 (이미 있으면 db.create_all()이 skip).
with app.app_context():
    db.create_all()

# 로컬 개발 실행부. 프로덕션은 gunicorn+eventlet으로 띄운다(Dockerfile CMD 참고):
#   gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:8012 app:app
if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀  CRACK SERVER v1.2.8  READY (dev server)")
    print("📈  Smart Road Safety Platform")
    print("="*50 + "\n")
    # 사용자가 0.0.0.0을 브라우저에 입력하는 오류를 방지하기 위해 기본값은 127.0.0.1로 바인딩
    host = os.getenv('FLASK_RUN_HOST', '127.0.0.1')
    # 디버그 모드는 .env의 FLASK_DEBUG=1일 때만 활성화 (배포 환경 기본값: off)
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host=host, port=8012, debug=debug_mode, allow_unsafe_werkzeug=True)
