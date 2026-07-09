import threading
from datetime import timedelta
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from extensions import db
from models import Report, Member, PointLog
from utils import extract_gps_from_exif, haversine, reverse_geocode, get_now_kst
from core.report_core import save_upload, sanitize_coord, strip_exif_and_convert

report_bp = Blueprint('report', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

@report_bp.route('/report', methods=['GET'])
def report_page():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    # kakao_js_key는 context_processor에 의해 주입됨
    return render_template('report.html')

@report_bp.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '파일이 없습니다.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '선택된 파일이 없습니다.'}), 400

    print(f"[UPLOAD] Original filename: '{file.filename}'")
    # base_dir을 앱 루트로 고정 (ai_core의 분석 경로 기준과 일치시켜 CWD 의존성 제거)
    saved = save_upload(file, base_dir=current_app.root_path)

    if not saved['ok']:
        print(f"[UPLOAD] REJECTED: filename='{saved['filename']}', ext='{saved['ext']}' not in allowed list")
        return jsonify({'success': False, 'message': f"허용되지 않는 파일 형식입니다. (감지된 확장자: {saved['ext']})"}), 400

    if saved['file_type'] == 'video':
        return jsonify({'success': True, 'message': '동영상 업로드 성공', 'path': saved['web_path']})

    print(f"[UPLOAD] Image saved to: {saved['save_path']}")

    # [수정] 업로드 즉시 GPS 메타데이터 추출 (프론트 유실 또는 HEIC 대비 서버 직접 추출)
    lat, lng = extract_gps_from_exif(saved['save_path'])
    print(f"[UPLOAD] GPS extraction result: lat={lat}, lng={lng}")

    # [NaN 방어] Pillow가 NaN을 반환할 경우 JSON 직렬화 오류 방지
    lat = sanitize_coord(lat)
    lng = sanitize_coord(lng)

    # GPS가 유효하면 즉시 역지오코딩하여 주소도 반환
    address = None
    if lat and lng:
        address = reverse_geocode(lat, lng)
        print(f"[UPLOAD] Reverse geocoded address: {address}")

    return jsonify({
        'success': True,
        'message': '이미지 업로드 성공 (GPS 추출 시도)',
        'path': saved['web_path'],
        'gps': {'lat': lat, 'lng': lng} if lat and lng else None,
        'address': address
    })

@report_bp.route('/api/report', methods=['POST'])
def submit_report():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': '제보를 위해 로그인이 필요합니다.'}), 401

    user_id = session.get('user_id')
    title = request.form.get('title', '')[:30]
    content = request.form.get('content')
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')
    address = request.form.get('address')
    # 제보 유형: 'road'(포트홀/싱크홀, 기본값) 또는 'drain'(배수구/우수관 막힘)
    category = request.form.get('category', 'road')
    if category not in ('road', 'drain'):
        category = 'road'

    file_path = None
    file_type = None

    if 'file' in request.files and request.files['file'].filename != '':
        # base_dir을 앱 루트로 고정 (ai_core의 분석 경로 기준과 일치)
        saved = save_upload(request.files['file'], base_dir=current_app.root_path)
        if not saved['ok']:
            return jsonify({'success': False, 'message': '이미지 또는 영상 형식이 올바르지 않습니다.'}), 400

        file_type = saved['file_type']
        file_path = saved['web_path']

        if file_type == 'image':
            # [핵심 수정] 프론트엔드에서 GPS를 이미 전달했는지 확인
            # 크롭된 이미지에는 EXIF가 없으므로 프론트 GPS가 있으면 해당 값을 우선 사용
            front_has_gps = bool(latitude and longitude)

            # 프론트에서 GPS를 못 보낸 경우에만 원본 파일에서 EXIF 추출 시도
            if not front_has_gps:
                print(f"[SUBMIT] Frontend didn't provide GPS. Attempting server-side extraction from uploaded file...")
                exif_lat, exif_lng = extract_gps_from_exif(saved['save_path'])
                if exif_lat and exif_lng:
                    latitude = exif_lat
                    longitude = exif_lng
                    print(f"[SUBMIT] ✅ Server-side GPS extraction succeeded: lat={latitude}, lng={longitude}")
                else:
                    print(f"[SUBMIT] ❌ Server-side GPS extraction also failed (file may be cropped/stripped)")
            else:
                print(f"[SUBMIT] ✅ Using GPS from frontend: lat={latitude}, lng={longitude}")

            # [개인정보 보호] 모든 이미지의 EXIF 메타데이터를 파기하고 재저장
            _, file_path = strip_exif_and_convert(saved['save_path'], saved['filename'], base_dir=current_app.root_path)

    lat = sanitize_coord(latitude)
    lng = sanitize_coord(longitude)

    if lat and lng and not address:
        address = reverse_geocode(lat, lng)

    # 중복 신고 제한
    if lat and lng:
        yesterday = get_now_kst() - timedelta(hours=24)
        duplicate = Report.query.filter(
            Report.user_id == user_id,
            Report.created_at >= yesterday,
            Report.latitude.isnot(None),
            Report.longitude.isnot(None)
        ).all()
        for r in duplicate:
            if haversine(lat, lng, r.latitude, r.longitude) <= 50:
                return jsonify({'success': False, 'message': '이미 1일 내 반경 50m 이내에 신고하신 건이 있습니다.'}), 400

    new_report = Report(
        user_id=user_id,
        title=title,
        content=content,
        latitude=lat,
        longitude=lng,
        address=address,
        file_path=file_path,
        file_type=file_type,
        status='AI 분석중',
        category=category
    )
    db.session.add(new_report)
    db.session.commit()

    # 크래커 포인트 적립 (신고 접수 +10점)
    member = Member.query.get(user_id)
    if member:
        member.points += 10
        db.session.add(PointLog(user_id=user_id, amount=10, reason='신고 접수'))
        db.session.commit()

    # AI 분석 트리거 (core/ai_core.py의 로직을 app에 바인딩된 래퍼로 비동기 실행, category로 모델 라우팅)
    if hasattr(current_app, 'run_ai_analysis'):
        ai_func = current_app._get_current_object().run_ai_analysis
        thread = threading.Thread(target=ai_func, args=(new_report.id, file_path, file_type, category))
        thread.start()

    return jsonify({'success': True, 'message': '제보가 성공적으로 접수되어 AI 분석을 시작합니다.', 'report_id': new_report.id})

@report_bp.route('/api/report/status/<int:report_id>', methods=['GET'])
def get_report_status(report_id):
    rpt = Report.query.get_or_404(report_id)
    return jsonify({
        'status': rpt.status,
        'is_analyzing': rpt.status == 'AI 분석중'
    })
