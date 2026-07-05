from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import db
from models import Member, Report, UserSettings, PointLog
from utils import check_profanity

my_bp = Blueprint('my', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

@my_bp.route('/mypage')
def mypage():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))
        
    member = Member.query.get(user_id)
    if not member:
        return redirect(url_for('auth.logout'))
        
    my_report_count = Report.query.filter_by(user_id=user_id).count()
    completed_count = Report.query.filter_by(user_id=user_id, status='처리 완료').count()
    settings = UserSettings.query.filter_by(user_id=user_id).first()
    notification_enabled = settings.notification_enabled if settings else True
    point_logs = PointLog.query.filter_by(user_id=user_id).order_by(PointLog.created_at.desc()).all()
            
    return render_template('mypage.html',
        my_report_count=my_report_count,
        completed_count=completed_count,
        notification_enabled=notification_enabled,
        is_admin=session.get('is_admin', False),
        member=member,
        point_logs=point_logs
    )


# 사용자가 신고 리스트를 조회하는 기능
@my_bp.route('/my-reports')
def my_reports():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    # 사용자가 직접 올린 신고 리스트를 최신순으로 가져옴.
    reports = Report.query.filter_by(user_id=user_id).order_by(Report.created_at.desc()).all()

    return render_template('myreports.html', reports=reports)

@my_bp.route('/api/mypage/profile', methods=['POST'])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
    
    data = request.get_json()
    member = Member.query.get(user_id)
    
    # 닉네임 변경 로직
    if 'nickname' in data:
        new_nickname = data['nickname'].strip()
        if not new_nickname:
            return jsonify({'success': False, 'message': '닉네임을 입력해주세요.'}), 400
        if len(new_nickname) > 20:
            return jsonify({'success': False, 'message': '닉네임은 최대 20자까지 가능합니다.'}), 400
        if not check_profanity(new_nickname):
            return jsonify({'success': False, 'message': '부적절한 단어가 포함되어 있습니다.'}), 400
            
        member.nickname = new_nickname
        db.session.commit()
        session['user_name'] = new_nickname # 세션 닉네임 동기화
        return jsonify({'success': True, 'message': '닉네임이 변경되었습니다.'})
    
    # 비밀번호 변경 로직
    if 'current_password' in data and 'new_password' in data:
        curr_pw = data['current_password']
        new_pw = data['new_password']
        
        if not check_password_hash(member.password_hash, curr_pw):
            return jsonify({'success': False, 'message': '현재 비밀번호가 일치하지 않습니다.'}), 400
            
        member.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        return jsonify({'success': True, 'message': '비밀번호가 변경되었습니다.'})
    
    # 관심지역 변경 로직
    if 'region_city' in data and 'region_district' in data:
        try:
            member.region_city = data['region_city']
            member.region_district = data['region_district']
            db.session.commit()
            return jsonify({'success': True, 'message': '관심지역이 저장되었습니다.'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'DB 저장 오류: {str(e)}'}), 500
        
    return jsonify({'success': False, 'message': '잘못된 요청입니다.'}), 400

@my_bp.route('/api/mypage/settings', methods=['POST'])
def update_settings():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
        
    data = request.get_json()
    enabled = data.get('notification_enabled', True)
    
    settings = UserSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = UserSettings(user_id=user_id, notification_enabled=enabled)
        db.session.add(settings)
    else:
        settings.notification_enabled = enabled
        
    db.session.commit()
    return jsonify({'success': True})

@my_bp.route('/api/withdraw', methods=['POST'])
def withdraw():
    """회원 탈퇴 API"""
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
        
    data = request.get_json()
    password_confirm = data.get('password_confirm', '')
    
    user = Member.query.get(session['user_id'])
    
    if not check_password_hash(user.password_hash, password_confirm):
        return jsonify({'success': False, 'message': '비밀번호가 일치하지 않습니다.'}), 400
        
    try:
        db.session.delete(user)
        db.session.commit()
        session.clear()
        return jsonify({'success': True, 'message': '그동안 이용해주셔서 감사합니다.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': '탈퇴 처리중 오류가 발생했습니다.'}), 500
