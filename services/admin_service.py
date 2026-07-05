from datetime import datetime

from core.region_core import normalize_region_name, parse_region_hierarchy
from core.admin_core import (
    build_groups,
    priority_score as _priority_score,
    status_rank as _status_rank,
    is_pending,
    is_urgent,
    summarize_dashboard,
    compute_member_stats,
    member_summary_comment,
    add_to_region_tree,
    build_period_bundle,
    build_all_trend,
    paginate,
)
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app
from sqlalchemy import text

from extensions import db, socketio
from utils import (
    safe_float as _safe_float,
    safe_int as _safe_int,
    parse_dt as _parse_dt,
    normalize_path as _normalize_path,
)

admin_bp = Blueprint('admin', __name__)


def _current_user_role():
    role = session.get('user_role') or session.get('role')
    if role:
        return role
    user_id = session.get('user_id')
    if not user_id:
        return 'user'
    row = db.session.execute(text("""
        SELECT
            COALESCE(role, CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END) AS role_value,
            is_admin,
            nickname,
            username
        FROM members
        WHERE id = :user_id
        LIMIT 1
    """), {'user_id': user_id}).mappings().first()
    if not row:
        return 'user'
    role_value = row.get('role_value') or ('admin' if _safe_int(row.get('is_admin')) == 1 else 'user')
    session['user_role'] = role_value
    session['role'] = role_value
    session['is_admin'] = role_value == 'admin' or _safe_int(row.get('is_admin')) == 1
    session['user_name'] = row.get('nickname') or row.get('username') or '관리자'
    return role_value


def _require_admin():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    if _current_user_role() != 'admin':
        return redirect(url_for('index'))
    return None


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
            r.address,
            r.status,
            r.reject_reason,
            r.region_name,
            r.last_checked_at,
            r.thumbnail_path,
            ai.is_damaged,
            ai.confidence,
            ai.damage_type,
            m.username,
            m.nickname,
            m.manager_region,
            COALESCE(m.role, CASE WHEN m.is_admin = 1 THEN 'admin' ELSE 'user' END) AS member_role,
            m.is_admin,
            m.active
        FROM report r
        {_latest_ai_join_sql()}
        LEFT JOIN members m ON m.id = r.user_id
        ORDER BY r.created_at DESC, r.id DESC
    """)
    rows = []
    for row in db.session.execute(sql).mappings().all():
        item = dict(row)
        item['created_at'] = _parse_dt(item.get('created_at'))
        item['risk_score'] = _safe_float(item.get('confidence'))
        # 경로 정규화 및 형식 판별 적용
        item['file_path'] = _normalize_path(item.get('file_path'))
        item['thumbnail_path'] = _normalize_path(item.get('thumbnail_path'))
        item['image_path'] = item['thumbnail_path'] or item['file_path'] or ''
        # 동영상 확장자 체크 추가
        if (item.get('file_path') or '').lower().endswith(('.mp4', '.mov', '.avi', '.m4v')):
            item['file_type'] = 'video'

        item['location'] = item.get('region_name') or item.get('address') or '위치 정보 없음'
        item['first_created_at'] = item['created_at']
        rows.append(item)
    return rows


def _hydrate_reports():
    reports = _fetch_reports()
    group_map = build_groups(reports)

    for item in reports:
        meta = group_map.get(item['id'], {})
        item['group_reporter_count'] = meta.get('group_reporter_count', 0)
        item['urgent_reason'] = meta.get('urgent_reason', '')
        item['priority_score'] = _priority_score(item)
        item['group_ids'] = meta.get('group_ids', [item['id']])
        item['representative_id'] = meta.get('representative_id', item['id'])

    representative_reports = [
        item for item in reports
        if _safe_int(item.get('id')) == _safe_int(item.get('representative_id'))
    ]

    return reports, representative_reports, group_map


def _member_name(row):
    return row.get('nickname') or row.get('username') or f"회원 {row.get('id')}"


def _member_uid(row):
    return row.get('username') or '-'


@admin_bp.route('/admin/dashboard')
def admin_dashboard():
    denied = _require_admin()
    if denied:
        return denied

    selected_tab = request.args.get('tab', 'urgent').strip() or 'pending'
    page = max(_safe_int(request.args.get('page', 1), 1), 1)
    per_page = 8

    reports, representative_reports, _ = _hydrate_reports()
    now = datetime.now()
    today = now.date()

    summary = summarize_dashboard(reports, now)

    if selected_tab == 'urgent':
        dashboard_items = [item for item in reports if is_urgent(item, now)]
        dashboard_section_title = '긴급 신고'
        dashboard_section_subtitle = '우선 검토가 필요한 신고입니다.'
    elif selected_tab == 'today':
        dashboard_items = [item for item in reports if item.get('created_at') and item['created_at'].date() == today]
        dashboard_section_title = '오늘 접수'
        dashboard_section_subtitle = '오늘 들어온 신고 목록입니다.'
    elif selected_tab == 'long_pending':
        dashboard_items = [item for item in reports if (item.get('status') or '') == '처리중']
        dashboard_section_title = '처리중'
        dashboard_section_subtitle = '현재 처리중인 신고 목록입니다.'
    elif selected_tab == 'rejected':
        dashboard_items = [item for item in reports if (item.get('status') or '') == '반려']
        dashboard_section_title = '반려 신고'
        dashboard_section_subtitle = '반려 처리된 신고 목록입니다.'
    else:
        selected_tab = 'pending'
        dashboard_items = [item for item in reports if is_pending(item)]
        dashboard_section_title = '미처리 신고'
        dashboard_section_subtitle = '현재 검토가 필요한 신고 목록입니다.'

    dashboard_items.sort(
        key=lambda x: (_priority_score(x, now), _safe_float(x.get('risk_score')), x.get('created_at') or datetime.min),
        reverse=True
    )

    dashboard_items, page, total_pages, total_count = paginate(dashboard_items, page, per_page)

    return render_template(
        'admin_dashboard.html',
        selected_tab=selected_tab,
        summary=summary,
        dashboard_items=dashboard_items,
        dashboard_section_title=dashboard_section_title,
        dashboard_section_subtitle=dashboard_section_subtitle,
        current_page=page,
        total_pages=total_pages,
        total_count=total_count,
        KAKAO_JS_KEY=current_app.config.get('KAKAO_JS_KEY', ''),
    )


@admin_bp.route('/admin/incidents')
def admin_incidents():
    member_id = request.args.get('member_id', type=int)
    denied = _require_admin()
    if denied:
        return denied

    quick_filter = request.args.get('quick_filter', '').strip()
    selected_status = request.args.get('status', '').strip()
    selected_risk = request.args.get('risk', '').strip()
    selected_region = request.args.get('region', '').strip()
    keyword = request.args.get('keyword', '').strip()
    sort_by = request.args.get('sort', 'latest').strip() or 'latest'
    sort_order = request.args.get('order', 'desc').strip().lower() or 'desc'
    page = max(_safe_int(request.args.get('page', 1), 1), 1)
    per_page = 8  # [USER REQUEST] 스크롤 방지를 위해 8개로 하향 조정

    reports, representative_reports, _ = _hydrate_reports()

    filtered = []
    for item in reports:
        status = item.get('status') or ''
        if status == '삭제':
            continue
        risk_score = _safe_float(item.get('risk_score'))
        region_name = normalize_region_name(item.get('region_name') or item.get('location') or '')
        title_text = (item.get('title') or '') + ' ' + (item.get('content') or '') + ' ' + (item.get('location') or '')

        if member_id and _safe_int(item.get('user_id')) != member_id:
            continue

        if quick_filter == 'pending' and status not in ('관리자 확인중', '접수완료'):
            continue
        if quick_filter == 'urgent' and not (risk_score >= 80 or _safe_int(item.get('group_reporter_count'), 0) >= 2):
            continue
        if selected_status and status != selected_status:
            continue
        if selected_risk == 'high' and risk_score < 80:
            continue
        if selected_risk == 'medium' and not (50 <= risk_score < 80):
            continue
        if selected_risk == 'low' and risk_score >= 50:
            continue
        if selected_region and region_name != selected_region:
            continue
        if keyword and keyword.lower() not in title_text.lower() and keyword not in str(item.get('id')):
            continue
        filtered.append(item)

    reverse = sort_order != 'asc'
    if sort_by == 'latest':
        filtered.sort(key=lambda x: (x.get('created_at') or datetime.min), reverse=reverse)
    elif sort_by == 'risk':
        filtered.sort(key=lambda x: (_safe_float(x.get('risk_score')), x.get('created_at') or datetime.min), reverse=reverse)
    elif sort_by == 'reports':
        filtered.sort(key=lambda x: (_safe_int(x.get('group_reporter_count'), 0), x.get('created_at') or datetime.min), reverse=reverse)
    elif sort_by == 'status':
        filtered.sort(key=lambda x: (_status_rank(x.get('status')), -_safe_float(x.get('risk_score')), x.get('created_at') or datetime.min), reverse=reverse)
    elif sort_by == 'pending':
        filtered.sort(key=lambda x: (_status_rank(x.get('status')), x.get('created_at') or datetime.min), reverse=(sort_order == 'asc'))
    else:
        sort_by = 'priority'
        filtered.sort(key=lambda x: (_priority_score(x), _safe_float(x.get('risk_score')), x.get('created_at') or datetime.min), reverse=reverse)

    incidents, page, total_pages, total_count = paginate(filtered, page, per_page)

    region_options = sorted({
        normalize_region_name(item.get('region_name') or item.get('location') or '')
        for item in representative_reports
        if normalize_region_name(item.get('region_name') or item.get('location') or '')
    })

    current_query = request.args.to_dict(flat=True)
    current_query.pop('page', None)
    if current_query:
        current_query_string = '&'.join(f"{key}={value}" for key, value in current_query.items() if value != '')
        if current_query_string:
            current_query_string = '&' + current_query_string
    else:
        current_query_string = ''

    return render_template(
        'admin_incidents.html',
        incidents=incidents,
        region_options=region_options,
        selected_region=selected_region,
        selected_status=selected_status,
        selected_risk=selected_risk,
        keyword=keyword,
        sort_by=sort_by,
        sort_order=sort_order,
        quick_filter=quick_filter,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        current_query_string=current_query_string,
        member_id=member_id,  # 추가
        KAKAO_JS_KEY=current_app.config.get('KAKAO_JS_KEY', ''),
    )

@admin_bp.route('/admin/incidents/group/<int:incident_id>')
def admin_incident_group(incident_id):
    denied = _require_admin()
    if denied:
        return jsonify({'success': False, 'message': '권한이 없습니다.'}), 403

    reports, _, group_map = _hydrate_reports()
    target = next((item for item in reports if _safe_int(item.get('id')) == incident_id), None)

    if not target:
        return jsonify({'success': False, 'message': '신고를 찾을 수 없습니다.'}), 404

    group_ids = group_map.get(incident_id, {}).get('group_ids', [incident_id])
    representative_id = group_map.get(incident_id, {}).get('representative_id')

    group_items = []
    for item in reports:
        if _safe_int(item.get('id')) in group_ids:
            created_at = item.get('created_at')
            group_items.append({
                'id': item.get('id'),
                'title': item.get('title') or '제목 없음',
                'member_name': item.get('nickname') or item.get('username') or f"회원 {item.get('user_id')}",
                'status': item.get('status') or '-',
                'created_at': created_at.strftime('%m-%d %H:%M') if created_at else '-',
                'is_representative': _safe_int(item.get('id')) == _safe_int(representative_id),
            })

    group_items.sort(
        key=lambda x: (0 if x['is_representative'] else 1, x['id'])
    )

    return jsonify({
        'success': True,
        'items': group_items
    })

@admin_bp.route('/incident/update-status', methods=['POST'])
def incident_update_status():
    denied = _require_admin()
    if denied:
        return denied

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        incident_id = _safe_int(payload.get('incident_id'))
        new_status = (payload.get('new_status') or '').strip()
        reject_reason = (payload.get('reject_reason') or '').strip()
    else:
        incident_id = _safe_int(request.form.get('incident_id'))
        new_status = (request.form.get('new_status') or '').strip()
        reject_reason = (request.form.get('reject_reason') or '').strip()

    if not incident_id or new_status not in ('관리자 확인중', '접수완료', '처리중', '처리완료', '반려'):
        if request.is_json:
            return jsonify({'ok': False, 'message': '잘못된 요청입니다.'}), 400
        return redirect(request.referrer or url_for('admin.admin_dashboard'))

    reports, _, group_map = _hydrate_reports()
    target = next((item for item in reports if _safe_int(item.get('id')) == incident_id), None)
    if not target:
        if request.is_json:
            return jsonify({'ok': False, 'message': '신고를 찾을 수 없습니다.'}), 404
        return redirect(request.referrer or url_for('admin.admin_dashboard'))

    target_ids = group_map.get(incident_id, {}).get('group_ids', [incident_id])
    placeholders = ','.join([f':id{i}' for i in range(len(target_ids))])
    params = {f'id{i}': rid for i, rid in enumerate(target_ids)}
    params.update({'new_status': new_status, 'reject_reason': reject_reason if new_status == '반려' else None, 'last_checked_at': datetime.now()})
    sql = text(f"""
        UPDATE report
        SET status = :new_status,
            reject_reason = :reject_reason,
            last_checked_at = :last_checked_at
        WHERE id IN ({placeholders})
    """)
    db.session.execute(sql, params)
    db.session.commit()

    socketio.emit('status_update', {'incident_id': incident_id, 'new_status': new_status}, namespace='/')
    if request.is_json:
        return jsonify({'ok': True, 'message': '상태가 변경되었습니다.'})
    return redirect(request.referrer or url_for('admin.admin_dashboard'))

@admin_bp.route('/admin/incidents/bulk-update', methods=['POST'])
def bulk_update_incidents():
    denied = _require_admin()
    if denied:
        return denied

    incident_ids = request.form.getlist('incident_ids')
    new_status = (request.form.get('new_status') or '').strip()
    reject_reason = (request.form.get('reject_reason') or '').strip()
    return_query = (request.form.get('return_query') or '').strip()

    if not incident_ids:
        return redirect(f"/admin/incidents?{return_query}" if return_query else url_for('admin.admin_incidents'))

    if new_status not in ('관리자 확인중', '접수완료', '처리중', '처리완료', '반려'):
        return redirect(f"/admin/incidents?{return_query}" if return_query else url_for('admin.admin_incidents'))

    incident_ids = [_safe_int(i) for i in incident_ids if _safe_int(i) > 0]
    if not incident_ids:
        return redirect(f"/admin/incidents?{return_query}" if return_query else url_for('admin.admin_incidents'))

    reports, _, group_map = _hydrate_reports()

    target_ids = set()
    for incident_id in incident_ids:
        grouped_ids = group_map.get(incident_id, {}).get('group_ids', [incident_id])
        for rid in grouped_ids:
            target_ids.add(_safe_int(rid))

    target_ids = [rid for rid in target_ids if rid > 0]
    if not target_ids:
        return redirect(f"/admin/incidents?{return_query}" if return_query else url_for('admin.admin_incidents'))

    placeholders = ','.join([f':id{i}' for i in range(len(target_ids))])
    params = {f'id{i}': rid for i, rid in enumerate(target_ids)}
    params.update({
        'new_status': new_status,
        'reject_reason': reject_reason if new_status == '반려' else None,
        'last_checked_at': datetime.now()
    })

    sql = text(f"""
        UPDATE report
        SET status = :new_status,
            reject_reason = :reject_reason,
            last_checked_at = :last_checked_at
        WHERE id IN ({placeholders})
    """)
    db.session.execute(sql, params)
    db.session.commit()

    for rid in target_ids:
        socketio.emit('status_update', {'incident_id': _safe_int(rid), 'new_status': new_status}, namespace='/')

    return redirect(f"/admin/incidents?{return_query}" if return_query else url_for('admin.admin_incidents'))


@admin_bp.route('/admin/members')
def admin_members():
    denied = _require_admin()
    if denied:
        return denied

    keyword = request.args.get('keyword', '').strip()
    role = request.args.get('role', '').strip()
    sort = request.args.get('sort', 'role').strip() or 'role'
    order = request.args.get('order', 'asc').strip().lower() or 'asc'
    page = max(_safe_int(request.args.get('page', 1), 1), 1)
    per_page = 8  # [USER REQUEST] 스크롤 방지를 위해 8개로 하향 조정

    sql = text("""
        SELECT
            id,
            username,
            nickname,
            created_at,
            is_admin,
            active,
            manager_region,
            email,
            COALESCE(role, CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END) AS role
        FROM members
        ORDER BY id DESC
    """)
    rows = [dict(r) for r in db.session.execute(sql).mappings().all()]

    members = []
    for row in rows:
        item = dict(row)
        item['name'] = _member_name(row)
        item['uid'] = _member_uid(row)
        item['created_at'] = _parse_dt(row.get('created_at'))
        members.append(item)

    if keyword:
        members = [m for m in members if keyword.lower() in (m.get('name') or '').lower() or keyword.lower() in (m.get('uid') or '').lower() or keyword == str(m.get('id'))]
    if role:
        members = [m for m in members if (m.get('role') or '') == role]

    reverse = order == 'desc'
    if sort == 'name':
        members.sort(key=lambda x: (x.get('name') or '').lower(), reverse=reverse)
    elif sort == 'uid':
        members.sort(key=lambda x: (x.get('uid') or '').lower(), reverse=reverse)
    elif sort == 'created_at':
        members.sort(key=lambda x: x.get('created_at') or datetime.min, reverse=reverse)
    elif sort == 'active':
        members.sort(key=lambda x: (_safe_int(x.get('active')), x.get('id')), reverse=reverse)
    elif sort == 'id':
        members.sort(key=lambda x: _safe_int(x.get('id')), reverse=reverse)
    else:
        sort = 'role'
        rank = {'admin': 1, 'manager': 2, 'user': 3}
        members.sort(key=lambda x: (rank.get(x.get('role') or 'user', 99), (x.get('name') or '').lower()), reverse=reverse)

    members, page, total_pages, _ = paginate(members, page, per_page)

    return render_template(
        'admin_members.html',
        members=members,
        keyword=keyword,
        role=role,
        sort=sort,
        order=order,
        page=page,
        total_pages=total_pages,
    )


# [NOTICE] 상세페이지(member_detail)에서는 모바일 브라우저의 PTR(Pull-to-Refresh) 기능을
# layout.html의 스크립트를 통해 '하드하게' 차단하고 있습니다.
# 이는 카카오 지도 로더와의 충돌을 방지하기 위함이므로, 상세페이지 레이아웃 유지 시 주의하십시오.
@admin_bp.route('/admin/members/<int:member_id>')
def admin_member_detail(member_id):
    denied = _require_admin()
    if denied:
        return denied

    member_row = db.session.execute(text("""
        SELECT
            id,
            username,
            nickname,
            created_at,
            is_admin,
            active,
            manager_region,
            email,
            COALESCE(role, CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END) AS role
        FROM members
        WHERE id = :member_id
        LIMIT 1
    """), {'member_id': member_id}).mappings().first()
    if not member_row:
        return redirect(url_for('admin.admin_members'))

    member = dict(member_row)
    member['name'] = _member_name(member)
    member['uid'] = _member_uid(member)
    member['created_at'] = _parse_dt(member.get('created_at'))

    reports, _, group_map = _hydrate_reports()
    member_reports = [r for r in reports if _safe_int(r.get('user_id')) == member_id]

    member_stats = compute_member_stats(member_reports)
    latest_posts = sorted(member_reports, key=lambda x: x.get('created_at') or datetime.min, reverse=True)[:4]

    return render_template(
        'admin_member_detail.html',
        member=member,
        member_stats=member_stats,
        member_incidents=latest_posts,
        member_summary_comment=member_summary_comment(member_stats),
    )

def _member_detail_redirect(member_id):
    page = request.form.get('page', request.args.get('page', 1))
    keyword = request.form.get('keyword', request.args.get('keyword', ''))
    role = request.form.get('role_filter', request.args.get('role', ''))
    sort = request.form.get('sort', request.args.get('sort', 'role'))
    order = request.form.get('order', request.args.get('order', 'asc'))

    return redirect(url_for(
        'admin.admin_member_detail',
        member_id=member_id,
        page=page,
        keyword=keyword,
        role=role,
        sort=sort,
        order=order
    ))


@admin_bp.route('/admin/members/<int:member_id>/role', methods=['POST'])
def admin_member_change_role(member_id):
    denied = _require_admin()
    if denied:
        return denied

    new_role = (request.form.get('role') or '').strip()
    if new_role not in ('admin', 'manager', 'user'):
        return _member_detail_redirect(member_id)

    db.session.execute(
        text("UPDATE members SET role = :role WHERE id = :member_id"),
        {'role': new_role, 'member_id': member_id}
    )
    db.session.commit()

    return _member_detail_redirect(member_id)


@admin_bp.route('/admin/members/<int:member_id>/suspend', methods=['POST'])
def admin_member_suspend(member_id):
    denied = _require_admin()
    if denied:
        return denied

    db.session.execute(
        text("UPDATE members SET active = 0 WHERE id = :member_id"),
        {'member_id': member_id}
    )
    db.session.commit()

    return _member_detail_redirect(member_id)


@admin_bp.route('/admin/members/<int:member_id>/unsuspend', methods=['POST'])
def admin_member_unsuspend(member_id):
    denied = _require_admin()
    if denied:
        return denied

    db.session.execute(
        text("UPDATE members SET active = 1 WHERE id = :member_id"),
        {'member_id': member_id}
    )
    db.session.commit()

    return _member_detail_redirect(member_id)


@admin_bp.route('/admin/statistics')
def admin_statistics():
    denied = _require_admin()
    if denied:
        return denied

    reports, _, _ = _hydrate_reports()
    now = datetime.now()

    # 1) 지역별 계층 집계
    region_data_map = {"all": {}}
    for r in reports:
        raw_address = r.get('region_name') or r.get('location') or ''
        parts = parse_region_hierarchy(raw_address)
        if not parts:
            add_to_region_tree(region_data_map["all"], ["기타"])
            continue
        add_to_region_tree(region_data_map["all"], parts)

    # 2) 기간별/전체 추이 데이터
    trend_data_map = {
        "all": {
            "7d": build_period_bundle(reports, 7, now),
            "30d": build_period_bundle(reports, 30, now),
            "all": build_all_trend(reports),
        }
    }

    # 3) 상단 요약
    total_reports = len(reports)
    statistics_summary = {
        "total_reports": total_reports,
        "pending_count": sum(1 for r in reports if (r.get('status') or '') in ('관리자 확인중', '접수완료')),
        "danger_count": sum(1 for r in reports if _safe_float(r.get('risk_score')) >= 80),
        "processing_count": sum(1 for r in reports if (r.get('status') or '') == '처리중'),
        "today_count": sum(1 for r in reports if r.get('created_at') and r['created_at'].date() == now.date()),
    }

    return render_template(
        'admin_statistics.html',
        region_data_map=region_data_map,
        trend_data_map=trend_data_map,
        statistics_summary=statistics_summary,
        page=1,
        total_pages=1,
        total_count=total_reports,
    )
