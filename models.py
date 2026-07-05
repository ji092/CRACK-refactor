from extensions import db
from utils import get_now_kst

class Member(db.Model):
    __tablename__ = 'members'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(80), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    points = db.Column(db.Integer, default=0) # 크래커 포인트
    created_at = db.Column(db.DateTime, default=get_now_kst)
    email = db.Column(db.String(120), unique=True, nullable=False)
    region_city = db.Column(db.String(50), nullable=True)
    region_district = db.Column(db.String(50), nullable=True)
    role = db.Column(db.String(20), nullable=True)  # 'admin' / 'manager' / 'user' (NULL이면 is_admin 기준으로 판단)
    active = db.Column(db.Boolean, default=True, nullable=False)
    manager_region = db.Column(db.String(50), nullable=True)

class Report(db.Model):
    __tablename__ = 'report'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('members.id', ondelete='CASCADE'), nullable=True)
    title = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    address = db.Column(db.String(255), nullable=True)
    file_path = db.Column(db.String(512), nullable=True)
    file_type = db.Column(db.String(50), nullable=True)
    thumbnail_path = db.Column(db.String(512), nullable=True) # AI가 생성한 썸네일 경로
    status = db.Column(db.String(20), default='담당자 확인중')
    reject_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    region_name = db.Column(db.String(50), nullable=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    author = db.relationship('Member', backref=db.backref('reports', lazy=True))

class AiResult(db.Model):
    __tablename__ = 'ai_results'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('report.id', ondelete='CASCADE'), nullable=False)
    is_damaged = db.Column(db.Boolean, default=False)
    confidence = db.Column(db.Float, nullable=True)
    damage_type = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    report = db.relationship('Report', backref=db.backref('ai_result', uselist=False, cascade='all, delete-orphan'))

class PointLog(db.Model):
    __tablename__ = 'point_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('members.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    member = db.relationship('Member', backref=db.backref('point_logs', lazy=True, order_by='PointLog.created_at.desc()'))

class UserSettings(db.Model):
    __tablename__ = 'user_settings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('members.id', ondelete='CASCADE'), nullable=False, unique=True)
    notification_enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    member = db.relationship('Member', backref=db.backref('settings', uselist=False))

class Notice(db.Model):
    __tablename__ = 'notices'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default='시스템')
    author_id = db.Column(db.Integer, db.ForeignKey('members.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    author = db.relationship('Member', backref=db.backref('notices', lazy=True))

class VideoDetection(db.Model):
    """동영상 프레임별 AI 검출 결과"""
    __tablename__ = 'video_detections'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('report.id', ondelete='CASCADE'), nullable=False)
    frame_time = db.Column(db.Float, nullable=False)        # 검출 시점 (초)
    class_name = db.Column(db.String(100), nullable=False)   # 검출 클래스명
    confidence = db.Column(db.Float, nullable=False)          # 신뢰도 (0~1)
    x1 = db.Column(db.Float, nullable=False)                  # 바운딩박스 좌상단 x (비율 0~1)
    y1 = db.Column(db.Float, nullable=False)                  # 바운딩박스 좌상단 y
    x2 = db.Column(db.Float, nullable=False)                  # 바운딩박스 우하단 x
    y2 = db.Column(db.Float, nullable=False)                  # 바운딩박스 우하단 y
    created_at = db.Column(db.DateTime, default=get_now_kst)
    report = db.relationship('Report', backref=db.backref('video_detections', lazy=True, cascade='all, delete-orphan'))

class CrackTalk(db.Model):
    __tablename__ = 'crack_talk'
    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey('members.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=get_now_kst)
    is_blinded = db.Column(db.Boolean, default=False, nullable=False)
    # Relationship
    author = db.relationship('Member', backref=db.backref('crack_talks', lazy=True, order_by='CrackTalk.created_at.asc()'))
