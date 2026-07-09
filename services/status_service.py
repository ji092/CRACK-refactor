from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, current_app
from werkzeug.utils import secure_filename
from extensions import db, socketio
from models import Report, CrackTalk, Member
from datetime import timedelta
from utils import check_profanity, get_now_kst

status_bp = Blueprint('status', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"
import os

def _normalize_path(path):
    if not path:
        return ''
    path = path.replace('\\', '/')
    if path.startswith('http') or path.startswith('data:'):
        return path
    if not path.startswith('/'):
        if path.startswith('uploads/'):
            path = '/' + path
        else:
            path = '/uploads/' + path
    return path

@status_bp.route('/status')
def status():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    one_day_ago = get_now_kst() - timedelta(hours=24)
    # [데이터 관리] 24시간이 지난 반려 게시물은 DB에서 영구 삭제 (사용자 요청 사항)
    expired_rejects = Report.query.filter(
        Report.user_id == user_id,
        Report.status == '반려',
        Report.created_at < one_day_ago
    ).all()

    if expired_rejects:
        for r in expired_rejects:
            # 삭제 시 관련 AI 결과도 cascade 등으로 인해 삭제되겠지만 명시적으로 처리 고려 가능
            db.session.delete(r)
        db.session.commit()

    db_reports = Report.query.filter(
        Report.user_id == user_id,
        Report.status != '삭제'
    ).order_by(Report.created_at.desc()).all()

    my_reports = []
    for r in db_reports:
        # 확장자 기반 file_type 판별 보강
        ext_video = (r.file_path or '').lower().endswith(('.mp4', '.mov', '.avi', '.m4v'))
        f_type = 'video' if ext_video else (r.file_type or 'image')
        
        my_reports.append({
            'id': r.id,
            'title': r.title or '제목 없음',
            'status': r.status,
            'date': r.created_at.strftime('%Y-%m-%d') if r.created_at else '',
            'file_path': _normalize_path(r.file_path),
            'thumbnail_path': _normalize_path(r.thumbnail_path),
            'file_type': f_type,
            'reject_reason': r.reject_reason
        })
    return render_template('status.html', reports=my_reports)

@status_bp.route('/api/cracktalk', methods=['GET'])
def get_cracktalk():
    is_admin = session.get('is_admin', False)
    # 최근 50개 메시지 조회
    talks = CrackTalk.query.order_by(CrackTalk.created_at.asc()).limit(50).all()
    result = []
    for t in talks:
        if t.is_blinded and not is_admin:
            # 일반 회원: 블라인드 처리된 메시지는 내용 숨김
            result.append({
                'id': t.id,
                'author_id': None,
                'nickname': '',
                'content': '',
                'date': t.created_at.strftime('%m-%d %H:%M'),
                'is_blinded': True
            })
        else:
            # 관리자 또는 정상 메시지: 전체 노출
            result.append({
                'id': t.id,
                'author_id': t.author_id,
                'nickname': t.author.nickname if t.author else '익명',
                'content': t.content,
                'date': t.created_at.strftime('%m-%d %H:%M'),
                'is_blinded': t.is_blinded
            })
    return jsonify(result)

@status_bp.route('/api/cracktalk', methods=['POST'])
def post_cracktalk():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    data = request.json
    content = data.get('content', '').strip()

    if not content:
        return jsonify({'success': False, 'message': '내용을 입력해주세요.'}), 400

    # 비속어 필터링 적용
    if not check_profanity(content):
        return jsonify({'success': False, 'message': '부적절한 단어가 포함되어 있습니다. 바른 말을 사용해 주세요.'}), 400

    # 크랙톡 작성은 크래커(포인트) 소모 없이 자유롭게 가능 (2026-07-09 차감 기능 제거)
    new_talk = CrackTalk(author_id=user_id, content=content)
    db.session.add(new_talk)
    try:
        db.session.commit()
        # [WEB-SOCKET] 실시간 CrackTalk 브로드캐스트
        session_user = Member.query.get(user_id)
        socketio.emit('new_message', {
            'id': new_talk.id,
            'author_id': new_talk.author_id,
            'nickname': session_user.nickname if session_user else '익명',
            'content': new_talk.content,
            'date': new_talk.created_at.strftime('%m-%d %H:%M'),
            'is_blinded': False
        }, namespace='/')
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': '저장 중 오류가 발생했습니다.'}), 500

    return jsonify({'success': True})


# 기존 DELETE 삭제 → PATCH 블라인드 토글로 교체
@status_bp.route('/api/cracktalk/blind/<int:talk_id>', methods=['PATCH'])
def toggle_blind_cracktalk(talk_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    talk = CrackTalk.query.get_or_404(talk_id)
    try:
        talk.is_blinded = not talk.is_blinded  # 블라인드 ↔ 노출 토글
        db.session.commit()
        return jsonify({'success': True, 'is_blinded': talk.is_blinded})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': '처리 중 오류가 발생했습니다.'}), 500


# alert_edit.html에서 호출하는 내 제보 수정 API
@status_bp.route('/api/report/<int:report_id>/update', methods=['POST'])
def update_report(report_id):
    from sqlalchemy import text as sa_text
    import cv2

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    report = Report.query.get_or_404(report_id)

    # DB에서 직접 admin 여부 확인
    row = db.session.execute(
        sa_text("SELECT is_admin, role FROM members WHERE id = :uid LIMIT 1"),
        {'uid': user_id}
    ).mappings().first()

    is_admin = False
    if row:
        is_admin = (row.get('is_admin') == 1) or (row.get('role') == 'admin')

    # 관리자는 모든 글 수정 가능, 일반 사용자는 본인 글만
    if not is_admin and int(report.user_id) != int(user_id):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    report.title = request.form.get('title')
    report.content = request.form.get('content')

    file = request.files.get('file')
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        is_video = ext in ('.mp4', '.mov', '.avi', '.m4v')

        # 영상/이미지 분리 저장
        if is_video:
            upload_dir = os.path.join(current_app.root_path, 'uploads', 'videos')
        else:
            upload_dir = os.path.join(current_app.root_path, 'uploads', 'images')

        os.makedirs(upload_dir, exist_ok=True)
        save_path = os.path.join(upload_dir, filename)
        file.save(save_path)

        if is_video:
            report.file_path = f"/uploads/videos/{filename}"
            report.file_type = 'video'

            # 영상 첫 프레임을 썸네일로 추출
            try:
                cap = cv2.VideoCapture(save_path)
                success, frame = cap.read()
                cap.release()
                if success:
                    thumb_name = os.path.splitext(filename)[0] + '_thumb.jpg'
                    thumb_dir = os.path.join(current_app.root_path, 'uploads', 'images')
                    os.makedirs(thumb_dir, exist_ok=True)
                    cv2.imwrite(os.path.join(thumb_dir, thumb_name), frame)
                    report.thumbnail_path = f"/uploads/images/{thumb_name}"
                else:
                    report.thumbnail_path = report.file_path
            except Exception:
                report.thumbnail_path = report.file_path
        else:
            report.file_path = f"/uploads/images/{filename}"
            report.file_type = 'image'
            report.thumbnail_path = f"/uploads/images/{filename}"

    try:
        db.session.commit()
        try:
            from threading import Thread
            file_path = report.file_path
            file_type = report.file_type or (
                'video' if (file_path or '').lower().endswith(('.mp4', '.mov', '.avi', '.m4v')) else 'image')
            report.status = '접수완료'  # AI 재분석 전 상태 초기화
            db.session.commit()

            # 제보 유형(category)을 함께 전달해 알맞은 모델로 재분석되게 함 (배수구 제보는 drain 모델로)
            category = getattr(report, 'category', 'road') or 'road'
            ai_func = current_app._get_current_object().run_ai_analysis
            t = Thread(target=ai_func, args=(report.id, file_path, file_type, category))
            t.daemon = True
            t.start()
        except Exception as ai_err:
            print(f"[AI 재분석 오류] {ai_err}")

        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


# alert_edit.html에서 호출하는 내 제보 삭제 API (실삭제 대신 status만 '삭제'로 변경)
@status_bp.route('/api/report/<int:report_id>/soft-delete', methods=['POST'])
def soft_delete_report(report_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    report = Report.query.get_or_404(report_id)

    # 본인 확인
    if int(report.user_id) != int(user_id):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    try:
        report.status = '삭제'
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})
