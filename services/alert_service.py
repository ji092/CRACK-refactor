import re
from datetime import datetime, timedelta

from core.region_core import normalize_region_name
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app
from sqlalchemy import text

from extensions import db
from models import Report, AiResult, Member, Notice, PointLog, VideoDetection
from utils import (
    safe_float as _safe_float,
    safe_int as _safe_int,
    parse_dt as _parse_dt,
    normalize_path as _normalize_path,
    haversine_m,
)

alert_bp = Blueprint('alert', __name__)

# [용어 정의] 상단바와 하단바를 제외한 실질적인 본문 영역을 '메인 콘텐츠 영역' 또는 '메인 영역'으로 정의합니다.
MAIN_CONTENT_AREA = "메인 콘텐츠 영역 (Main Content Area)"

VISIBLE_USER_STATUSES = {'접수완료', '처리중', '처리완료'}
ADMIN_ALERT_STATUSES = {'관리자 확인중', '접수완료', '처리중', '처리완료', '반려'}


def _current_user_role():
    # 세션 캐시 대신 실시간 DB 체크를 선호하거나, 최소한 admin 여부는 확실히 체크해야 함
    user_id = session.get('user_id')
    if not user_id:
        return 'user'

    sql = text("""
        SELECT
            COALESCE(role, CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END) AS role_value,
            is_admin,
            nickname,
            username
        FROM members
        WHERE id = :user_id
        LIMIT 1
    """)
    row = db.session.execute(sql, {'user_id': user_id}).mappings().first()
    if not row:
        return 'user'

    # is_admin이 1이면 role에 상관없이 admin으로 취급 (권한 충돌 방지)
    is_admin_db = _safe_int(row.get('is_admin'))
    role_db = row.get('role_value')
    
    if is_admin_db == 1:
        role_value = 'admin'
    else:
        role_value = role_db or 'user'

    session['user_role'] = role_value
    session['role'] = role_value
    session['is_admin'] = (is_admin_db == 1) or (role_value == 'admin')
    session['user_name'] = row.get('nickname') or row.get('username') or '사용자'
    return role_value

def _get_manager_region():
    region = session.get('manager_region')
    if region:
        return region

    user_id = session.get('user_id')
    if not user_id:
        return None

    sql = text("""
        SELECT manager_region
        FROM members
        WHERE id = :user_id
        LIMIT 1
    """)

    row = db.session.execute(sql, {'user_id': user_id}).mappings().first()

    region = row.get('manager_region') if row else None
    session['manager_region'] = region

    return region


def _latest_ai_join_sql():
    return """
        LEFT JOIN (
            SELECT a1.*
            FROM ai_results a1
            INNER JOIN (
                SELECT report_id, MAX(id) AS max_id
                FROM ai_results
                GROUP BY report_id
            ) a2 ON a1.id = a2.max_id
        ) ai ON ai.report_id = r.id
    """


def _fetch_reports():
    sql = text(f"""
        SELECT
            r.id,
            r.title,
            r.content,
            r.latitude,
            r.longitude,
            r.file_path,
            r.file_type,
            r.created_at,
            r.user_id,
            r.status,
            r.reject_reason,
            r.region_name,
            r.last_checked_at,
            r.thumbnail_path,
            r.address,
            ai.is_damaged,
            ai.confidence,
            ai.damage_type,
            m.username,
            m.nickname,
            m.manager_region,
            m.region_city,
            m.region_district,
            COALESCE(m.role, CASE WHEN m.is_admin = 1 THEN 'admin' ELSE 'user' END) AS member_role,
            m.is_admin,
            m.active
        FROM report r
        {_latest_ai_join_sql()}
        LEFT JOIN members m ON m.id = r.user_id
        ORDER BY r.created_at DESC, r.id DESC
    """)
    return [dict(row) for row in db.session.execute(sql).mappings().all()]


def _build_groups(items):
    normalized = []
    for raw in items:
        item = dict(raw)
        item['created_at'] = _parse_dt(item.get('created_at'))
        item['risk_score'] = _safe_float(item.get('confidence'))
        item['image_path'] = item.get('thumbnail_path') or item.get('file_path') or ''
        item['region_name'] = normalize_region_name(item.get('region_name') or item.get('content'))
        item['location'] = item.get('region_name') or item.get('content') or '위치 정보 없음'
        normalized.append(item)

    groups = []
    visited = set()
    for item in normalized:
        item_id = item['id']
        if item_id in visited:
            continue
        queue = [item]
        component = []
        visited.add(item_id)
        while queue:
            current = queue.pop()
            component.append(current)
            current_dt = current.get('created_at')
            for other in normalized:
                other_id = other['id']
                if other_id in visited:
                    continue
                other_dt = other.get('created_at')
                if current_dt is None or other_dt is None:
                    continue
                if abs((current_dt - other_dt).total_seconds()) > 86400:
                    continue
                if haversine_m(current.get('latitude'), current.get('longitude'), other.get('latitude'), other.get('longitude')) > 50:
                    continue
                visited.add(other_id)
                queue.append(other)
        groups.append(component)

    group_map = {}
    for group in groups:
        distinct_users = len({g.get('user_id') for g in group if g.get('user_id') is not None})
        representative = max(
            group,
            key=lambda x: (_safe_float(x.get('risk_score')), x.get('created_at') or datetime.min, x.get('id') or 0)
        )
        for member in group:
            urgent_reasons = []
            if _safe_float(representative.get('risk_score')) >= 80:
                urgent_reasons.append('고위험')
            if distinct_users >= 3:
                urgent_reasons.append('반복 제보')
            created_at = member.get('created_at')
            status = member.get('status') or ''
            if created_at and status in ('접수완료', '관리자 확인중') and (datetime.now() - created_at).total_seconds() >= 86400:
                urgent_reasons.append('장기 미처리')
            member['group_reporter_count'] = distinct_users or 1
            member['urgent_reason'] = ', '.join(urgent_reasons)
            group_map[member['id']] = {
                'group_ids': [g['id'] for g in group],
                'representative_id': representative.get('id'),
                'group_reporter_count': distinct_users or 1,
                'urgent_reason': member['urgent_reason'],
            }
    return normalized, group_map


def _selected_point():
    lat = request.args.get('lat') or session.get('selected_lat')
    lng = request.args.get('lng') or session.get('selected_lng')
    return _safe_float(lat, None), _safe_float(lng, None)


def _status_class(status):
    if status == '접수완료':
        return 'status-received'
    if status == '처리중':
        return 'status-processing'
    if status == '처리완료':
        return 'status-done'
    if status == '반려':
        return 'status-rejected'
    if status == '관리자 확인중':
        return 'status-review'
    return 'status-default'


def _risk_payload(score):
    score = _safe_float(score)
    if score >= 80:
        return 'high', 'risk-high'
    if score >= 50:
        return 'medium', 'risk-medium'
    return 'low', 'risk-low'


def _priority_score(item):
    score = 0
    status = item.get('status') or ''
    risk_score = _safe_float(item.get('risk_score'))
    reporters = _safe_int(item.get('group_reporter_count'), 1)
    created_at = item.get('created_at')
    if status in ('접수완료', '관리자 확인중'):
        score += 100
    if risk_score >= 80:
        score += 50
    elif risk_score >= 50:
        score += 20
    if reporters >= 5:
        score += 40
    elif reporters >= 3:
        score += 30
    elif reporters >= 2:
        score += 10
    if created_at and status in ('접수완료', '관리자 확인중') and (datetime.now() - created_at).total_seconds() >= 86400:
        score += 40
    return score


def _serialize_alert_item(item, selected_lat=None, selected_lng=None):
    risk_text, risk_class = _risk_payload(item.get('risk_score'))
    distance_m = 0
    if selected_lat is not None and selected_lng is not None:
        distance_m = int(round(haversine_m(selected_lat, selected_lng, item.get('latitude'), item.get('longitude'))))

    # 주소 간소화
    full_address = item.get('address') or item.get('location') or ''
    simplified_address = full_address
    if full_address:
        match = re.search(r'([가-힣]+[시도])\s+([가-힣]+[구군시])\s+([가-힣0-9]+[로길])', full_address)
        if match:
            simplified_address = match.group(0)

    return {
        'id': item.get('id'),
        'report_id': item.get('id'),
        'title': item.get('title') or '제목 없음',
        'content': item.get('content') or '',
        'location': item.get('location') or '위치 정보 없음',
        'address': simplified_address,
        'full_address': full_address,
        'distance_m': distance_m,
        'risk_text': risk_text,
        'risk_class': risk_class,
        'risk_score': int(round(_safe_float(item.get('risk_score')))),
        'confidence': _safe_float(item.get('risk_score')),
        'damage_type': item.get('damage_type') or 'N/A',
        'status': item.get('status') or '-',
        'status_class': _status_class(item.get('status') or ''),
        'group_reporter_count': _safe_int(item.get('group_reporter_count'), 1),
        'reporter_count': _safe_int(item.get('group_reporter_count'), 1),
        'created_at': item.get('created_at').strftime('%m-%d %H:%M') if item.get('created_at') else '-',
        'time': item.get('created_at').strftime('%Y-%m-%d %H:%M:%S') if item.get('created_at') else '알 수 없음',
        'created_at_obj': item.get('created_at'),
        'image_path': _normalize_path(item.get('thumbnail_path') or item.get('file_path')),
        'file_path': _normalize_path(item.get('file_path')),
        'thumbnail_path': _normalize_path(item.get('thumbnail_path')),
        'original_file_path': _normalize_path(item.get('file_path')),
        'file_type': 'video' if (item.get('file_path') or '').lower().endswith(('.mp4', '.mov', '.avi', '.m4v')) else (item.get('file_type') or 'image'),
        'latitude': item.get('latitude'),
        'longitude': item.get('longitude'),
        'lat': item.get('latitude'),
        'lng': item.get('longitude'),
        'reject_reason': item.get('reject_reason') or '',
        'username': item.get('username') or '',
        'nickname': item.get('nickname') or '',
        'reporter_name': item.get('nickname') or item.get('username') or '알 수 없음',
        'urgent_reason': item.get('urgent_reason') or '',
        'priority_score': _priority_score(item),
    }


def _load_alert_items():
    raw = _fetch_reports()
    normalized, group_map = _build_groups(raw)
    for item in normalized:
        meta = group_map.get(item['id'], {})
        item['group_reporter_count'] = meta.get('group_reporter_count', 1)
        item['urgent_reason'] = meta.get('urgent_reason', '')
        item['group_ids'] = meta.get('group_ids', [item['id']])
    return normalized

def _split_region_levels(region_text):
    if not region_text:
        return None, None, None

    parts = region_text.split()

    level1 = parts[0] if len(parts) > 0 else None  # 경기도
    level2 = parts[1] if len(parts) > 1 else None  # 수원시
    level3 = parts[2] if len(parts) > 2 else None  # 영통구

    return level1, level2, level3


def _get_user_interest_region():
    """현재 로그인한 유저의 관심지역(region_city, region_district)을 가져옵니다."""
    user_id = session.get('user_id')
    if not user_id:
        return '', ''
    sql = text("""
        SELECT region_city, region_district
        FROM members
        WHERE id = :user_id
        LIMIT 1
    """)
    row = db.session.execute(sql, {'user_id': user_id}).mappings().first()
    if not row:
        return '', ''
    return row.get('region_city') or '', row.get('region_district') or ''


@alert_bp.route('/alert')
def alert_page():
    role = _current_user_role()
    items = _load_alert_items()
    selected_lat, selected_lng = _selected_point()

    # =========================
    # 관리자 / 매니저
    # =========================
    if role in ('admin', 'manager'):
        region_filter_on = request.args.get('region_filter', 'on') == 'on'
        manager_region = _get_manager_region()
        manager_region = normalize_region_name(manager_region) or manager_region

        # 🔸 지역 정보가 없는 경우 (순수 우선순위 점수 + 최신순)
        if not manager_region:
            filtered = [
                item for item in items
                if (item.get('status') or '') in ADMIN_ALERT_STATUSES
            ]
            filtered.sort(
                key=lambda x: (
                    _priority_score(x),
                    _safe_float(x.get('risk_score')),
                    x.get('created_at') or datetime.min
                ),
                reverse=True
            )
        # 🔸 담당 지역이 설정된 경우 (지역 가중치 + 우선순위 점수)
        else:
            m_lv1, m_lv2, m_lv3 = _split_region_levels(manager_region)
            priority_list = []
            secondary_list = []
            others = []

            for item in items:
                status = item.get('status') or ''
                if status not in ADMIN_ALERT_STATUSES:
                    continue

                raw_region = item.get('region_name') or ''
                normalized_region = normalize_region_name(raw_region) or raw_region
                r_lv1, r_lv2, r_lv3 = _split_region_levels(normalized_region)

                if m_lv1 == r_lv1 and m_lv2 == r_lv2 and m_lv3 == r_lv3:
                    priority_list.append(item)
                elif m_lv1 == r_lv1 and m_lv2 == r_lv2:
                    secondary_list.append(item)
                else:
                    others.append(item)

            def sort_func(x):
                return (
                    _priority_score(x),
                    _safe_float(x.get('risk_score')),
                    x.get('created_at') or datetime.min
                )

            priority_list.sort(key=sort_func, reverse=True)
            secondary_list.sort(key=sort_func, reverse=True)
            others.sort(key=sort_func, reverse=True)

            if region_filter_on:
                filtered = priority_list + secondary_list
            else:
                filtered = priority_list + secondary_list + others

        alerts = [_serialize_alert_item(item, selected_lat, selected_lng) for item in filtered]

        return render_template(
            'alert.html',
            alerts=alerts,
            kakao_js_key=current_app.config.get('KAKAO_JS_KEY', ''),
            region_filter_on=region_filter_on,
            current_role=role
        )

    # =========================
    # 🔥 일반 사용자 (관심지역 우선 정렬 적용)
    # =========================
    region_city, region_district = _get_user_interest_region()

    filtered = []
    user_id = session.get('user_id')
    
    for item in items:
        status = item.get('status') or ''
        risk_score = _safe_float(item.get('risk_score'))
        reporters = _safe_int(item.get('group_reporter_count'), 1)
        item_user_id = item.get('member_id') or item.get('user_id')

        # 상태 기반 기본 필터
        if status not in VISIBLE_USER_STATUSES:
            # 내 글이면 관리자 확인중이나 AI 분석중인 상태여도 보여줌
            if not (user_id and item_user_id == user_id and status != '반려'):
                continue

        # [필터 완화] 위험도가 낮아도 모든 접수된 건은 보여주도록 수정 (사용자 요청 반영)
        # 이전: if risk_score >= 80 or reporters >= 3:
        if True: # 모든 게시물 노출
            filtered.append(item)

    # 관심지역 기반 우선 정렬
    if region_city or region_district:
        def user_sort_key(item):
            addr = item.get('address') or item.get('location') or ''
            is_region_match = False
            if region_city and region_city in addr:
                if region_district:
                    if region_district in addr:
                        is_region_match = True
                else:
                    is_region_match = True

            match_score = 0 if is_region_match else 1
            ts = item['created_at'].timestamp() if item.get('created_at') else 0
            return (match_score, -ts)

        filtered.sort(key=user_sort_key)
    else:
        filtered.sort(
            key=lambda x: (
                _safe_float(x.get('risk_score')),
                _safe_int(x.get('group_reporter_count'), 1),
                x.get('created_at') or datetime.min
            ),
            reverse=True
        )

    alerts = [_serialize_alert_item(item, selected_lat, selected_lng) for item in filtered]

    # 공지사항 조회
    notices = []
    try:
        notices_query = Notice.query.order_by(Notice.created_at.desc()).all()
        for n in notices_query:
            notices.append({
                'id': n.id,
                'title': n.title,
                'content': n.content,
                'category': n.category,
                'date': n.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'author': n.author.nickname or n.author.username if n.author else '관리자'
            })
    except Exception:
        pass

    return render_template(
        'alert.html',
        alerts=alerts,
        notices=notices,
        kakao_js_key=current_app.config.get('KAKAO_JS_KEY', ''),
        current_role=role
    )







# =====================================================
# 아래는 기존 라우트 (상세보기, 상태 업데이트, 공지, 동영상 API)
# =====================================================

# [NOTICE] 상세페이지(alert_view)에서는 모바일 브라우저의 PTR(Pull-to-Refresh) 기능을 
# layout.html의 스크립트를 통해 '하드하게' 차단하고 있습니다. 
# 이는 카카오 지도 로더와의 충돌을 방지하기 위함이므로, 상세페이지 레이아웃 유지 시 주의하십시오.
@alert_bp.route('/alert/view/<int:report_id>')
def alert_view(report_id):
    rpt = Report.query.get_or_404(report_id)
    ai_res = AiResult.query.filter_by(report_id=rpt.id).first()

    reporter = Member.query.get(rpt.user_id)
    reporter_name = reporter.nickname if reporter and reporter.nickname else (reporter.username if reporter else '알 수 없음')

    detail = {
        'id': rpt.id,
        'title': rpt.title or '도로 파손 신고',
        'content': rpt.content,
        'status': rpt.status,
        'time': rpt.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'address': rpt.address,
        'lat': rpt.latitude,
        'lng': rpt.longitude,
        'file_path': _normalize_path(rpt.file_path),
        'thumbnail_path': _normalize_path(rpt.thumbnail_path),
        'file_type': 'video' if (rpt.file_path or '').lower().endswith(('.mp4', '.mov', '.avi', '.m4v')) else (rpt.file_type or 'image'),
        'reporter_name': reporter_name,
        'confidence': ai_res.confidence if ai_res else 0,
        'damage_type': ai_res.damage_type if ai_res else 'N/A'
    }

    # [SECURITY POLICY] 상세 페이지 가시성 권한 제어
    # 1. 관리자(is_admin): 지도, 첨부 동영상, 첨부 사진, AI 분석 상세 데이터를 모두 열람 가능
    # 2. 일반 사용자: 개인정보 및 분석 데이터 보호를 위해 지도(Location) 및 기본 텍스트만 열람 가능
    return render_template('alert_view_v2.html', detail=detail, is_admin=session.get('is_admin', False))


@alert_bp.route('/api/admin/report/<int:report_id>/status', methods=['POST'])
def update_report_status(report_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    data = request.get_json()
    new_status = data.get('status')
    if not new_status:
        return jsonify({'success': False, 'message': '상태 값이 누락되었습니다.'}), 400

    try:
        rpt = Report.query.get_or_404(report_id)
        old_status = rpt.status
        rpt.status = new_status
        if data.get('reject_reason'):
            rpt.reject_reason = data.get('reject_reason')

        # 크래커 포인트 처리
        if rpt.user_id:
            member = Member.query.get(rpt.user_id)
            if member:
                if new_status == '처리 완료' and old_status != '처리 완료':
                    member.points += 20
                    db.session.add(PointLog(user_id=rpt.user_id, amount=20, reason='신고 처리 완료 보상'))
                elif new_status == '반려' and old_status != '반려':
                    member.points = max(0, member.points - 10)
                    db.session.add(PointLog(user_id=rpt.user_id, amount=-10, reason='신고 반려 (포인트 차감)'))

        db.session.commit()
        return jsonify({'success': True, 'message': f'상태가 {new_status}(으)로 변경되었습니다.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@alert_bp.route('/api/admin/report/<int:report_id>/delete', methods=['POST'])
def delete_report(report_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    try:
        rpt = Report.query.get_or_404(report_id)
        # 삭제 사유는 필요 시 로그로 남기거나 별도 필드에 기록 (현재는 즉시 삭제)
        db.session.delete(rpt)
        db.session.commit()
        return jsonify({'success': True, 'message': '제보가 삭제되었습니다.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@alert_bp.route('/api/admin/notice', methods=['POST'])
def add_notice():
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    data = request.get_json()
    title = data.get('title')
    content = data.get('content')
    category = data.get('category', '일반')

    if not title or not content:
        return jsonify({'success': False, 'message': '제목과 내용을 입력해주세요.'}), 400

    try:
        new_notice = Notice(
            title=title,
            content=content,
            category=category,
            author_id=session.get('user_id')
        )
        db.session.add(new_notice)
        db.session.commit()
        return jsonify({'success': True, 'message': '공지사항이 등록되었습니다.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@alert_bp.route('/api/report/<int:report_id>/detections')
def get_video_detections(report_id):
    """동영상 프레임별 AI 검출 결과 API"""
    detections = VideoDetection.query.filter_by(report_id=report_id).order_by(VideoDetection.frame_time).all()
    return jsonify([{
        'time': d.frame_time,
        'class': d.class_name,
        'conf': round(d.confidence * 100, 1),
        'x1': d.x1, 'y1': d.y1,
        'x2': d.x2, 'y2': d.y2
    } for d in detections])
