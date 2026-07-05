import os
import json
import re
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from models import Member, PointLog
from utils import check_profanity

auth_bp = Blueprint('auth', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = Member.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['user_name'] = user.nickname if user.nickname else user.username
            session['is_admin'] = user.is_admin
            session['user_role'] = 'admin' if user.is_admin else 'user'
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="아이디 또는 비밀번호가 잘못되었습니다.")
            
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ... 기존 import 생략

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        nickname = request.form.get('nickname')
        email = request.form.get('email')

        # 1. 이메일 형식 및 @ 앞부분 영문/숫자 체크
        # 규칙: 시작은 영문/숫자, @ 앞까지 영문/숫자만 허용
        email_pattern = r'^[a-zA-Z0-9]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

        if not email or not re.match(email_pattern, email):
            return render_template('signup.html', error="이메일 형식이 올바르지 않거나, 아이디 부분에 특수문자를 사용할 수 없습니다.")
        # 기본 검증
        if not nickname or len(nickname) > 20:
            return render_template('signup.html', error="닉네임은 1자 이상 20자 이하로 입력해주세요.")

        if not check_profanity(nickname):
            return render_template('signup.html', error="닉네임에 부적절한 단어가 포함되어 있습니다.")

        # 중복 검사 (아이디 & 이메일)
        if Member.query.filter_by(username=username).first():
            return render_template('signup.html', error="이미 존재하는 아이디입니다.")

        if Member.query.filter_by(email=email).first():  # 이메일 중복 체크 추가
            return render_template('signup.html', error="이미 등록된 이메일입니다.")

        hashed_pw = generate_password_hash(password)
        # DB 모델에 email 필드가 있다고 가정 (new_user 생성 시 추가)
        new_user = Member(username=username, password_hash=hashed_pw, nickname=nickname, email=email, points=100)
        db.session.add(new_user)
        db.session.commit()

        # 크래커 포인트 적립 (회원가입 축하 +100점)
        db.session.add(PointLog(user_id=new_user.id, amount=100, reason='회원가입 축하'))
        db.session.commit()

        return redirect(url_for('auth.login'))

    return render_template('signup.html')


# 이메일 중복 확인 API 추가
@auth_bp.route('/api/check_email', methods=['POST'])
def check_email():
    data = request.get_json()
    email = data.get('email')

    # 정규식 패턴 (기존과 동일하게 유지)
    email_pattern = r'^[a-zA-Z0-9]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

    if not email or not re.match(email_pattern, email):
        return jsonify({'available': False, 'message': '영문/숫자 조합의 올바른 이메일 형식을 입력해주세요.'}), 400

    user = Member.query.filter_by(email=email).first()
    if user:
        return jsonify({'available': False, 'message': '이미 사용 중인 이메일입니다.'})
    else:
        return jsonify({'available': True, 'message': '사용 가능한 이메일입니다.'})


@auth_bp.route('/api/check_id', methods=['POST'])
def check_id():
    data = request.get_json()
    username = data.get('username')
    
    if not username:
        return jsonify({'available': False, 'message': '아이디를 입력해주세요.'}), 400
        
    user = Member.query.filter_by(username=username).first()
    if user:
        return jsonify({'available': False, 'message': '이미 존재하는 아이디입니다.'})
    else:
        return jsonify({'available': True, 'message': '사용 가능한 아이디입니다.'})


@auth_bp.route('/api/find-id', methods=['POST'])
def find_id():
    data = request.get_json()
    nickname = data.get('name')
    email = data.get('email')

    # DB에서 이름(nickname)과 이메일이 모두 일치하는 사용자 검색
    user = Member.query.filter_by(nickname=nickname, email=email).first()

    if user:
        return jsonify({
            'success': True,
            'username': user.username,
            'message': f"찾으시는 아이디는 '{user.username}' 입니다."
        })
    else:
        return jsonify({
            'success': False,
            'message': "일치하는 회원 정보가 없습니다."
        })


@auth_bp.route('/api/find-pw', methods=['POST'])
def find_password():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')

    # 아이디와 이메일이 모두 일치하는 유저 찾기
    user = Member.query.filter_by(username=username, email=email).first()

    if user:
        # 보안상 실제 비밀번호를 보여줄 순 없으므로, 확인되었다는 메시지만 전달
        # 실제 서비스라면 여기서 비밀번호 재설정 페이지로 유도하거나 임시 비번을 보냅니다.
        return jsonify({
            'success': True,
            'message': "사용자 정보가 확인되었습니다. 비밀번호를 재설정하시겠습니까?"
        })
    else:
        return jsonify({
            'success': False,
            'message': "일치하는 회원 정보가 없습니다."
        })


@auth_bp.route('/api/reset-pw', methods=['POST'])
def reset_pw():
    data = request.get_json()
    username = data.get('username')
    new_password = data.get('password')

    user = Member.query.filter_by(username=username).first()
    if user:
        # 새로운 비밀번호 해싱 후 저장
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        return jsonify({'success': True, 'message': '비밀번호가 성공적으로 변경되었습니다.'})

    return jsonify({'success': False, 'message': '사용자를 찾을 수 없습니다.'})