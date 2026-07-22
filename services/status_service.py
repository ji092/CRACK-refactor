from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from extensions import db, socketio
from models import Report, CrackTalk, Member
from datetime import timedelta
from utils import check_profanity, get_now_kst

status_bp = Blueprint('status', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

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
