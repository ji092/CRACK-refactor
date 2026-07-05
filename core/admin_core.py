"""관리자 통계·신고 그룹핑 코어 로직.

DB/Flask에 의존하지 않는 순수 계산 함수 모음.
(기존 services/admin_service.py의 계산 로직을 이동. 시간 의존 함수는
now 파라미터를 받아 테스트 시 시각을 고정할 수 있게 개선)
"""
from datetime import datetime, timedelta

from utils import safe_float, safe_int, haversine_m

# 그룹핑 기준: 반경 50m, 24시간 이내
GROUP_RADIUS_M = 50
GROUP_WINDOW_SEC = 86400


def status_rank(status):
    """상태 문자열을 정렬용 순위로 변환한다 (낮을수록 우선)."""
    order = {
        '관리자 확인중': 0,
        '접수완료': 1,
        '신고 처리중': 2,
        '처리중': 3,
        '처리완료': 4,
        '반려': 5,
        '삭제': 6,
    }
    return order.get((status or '').strip(), 99)


def priority_score(item, now=None):
    """신고 1건의 우선순위 점수를 계산한다.

    미처리(+100), 위험도 80↑(+50)/50↑(+20), 반복 제보(+10~40), 처리 지연 24h↑(+40)
    """
    if now is None:
        now = datetime.now()
    score = 0
    status = item.get('status') or ''
    risk_score = safe_float(item.get('risk_score'))
    repeat_count = safe_int(item.get('group_reporter_count'), 0)
    created_at = item.get('created_at')

    if status in ('접수완료', '관리자 확인중'):
        score += 100

    if risk_score >= 80:
        score += 50
    elif risk_score >= 50:
        score += 20

    if repeat_count >= 4:
        score += 40
    elif repeat_count >= 2:
        score += 30
    elif repeat_count >= 1:
        score += 10

    if created_at and status in ('접수완료', '관리자 확인중') and (now - created_at).total_seconds() >= GROUP_WINDOW_SEC:
        score += 40

    return score


def build_groups(items, now=None):
    """반경 50m·24시간 이내 신고를 그룹으로 묶고, 각 신고 id → 그룹 메타 dict를 반환한다.

    각 item에 group_reporter_count / urgent_reason / priority_score 키를 채워넣는 부수효과 포함
    (기존 admin_service._build_groups 동작 그대로).
    """
    if now is None:
        now = datetime.now()
    groups = []
    visited = set()
    for item in items:
        if item['id'] in visited:
            continue
        component = []
        queue = [item]
        visited.add(item['id'])
        while queue:
            current = queue.pop()
            component.append(current)
            current_dt = current.get('created_at')
            for other in items:
                if other['id'] in visited:
                    continue
                other_dt = other.get('created_at')
                if current_dt is None or other_dt is None:
                    continue
                if abs((current_dt - other_dt).total_seconds()) > GROUP_WINDOW_SEC:
                    continue
                if haversine_m(current.get('latitude'), current.get('longitude'), other.get('latitude'), other.get('longitude')) > GROUP_RADIUS_M:
                    continue
                visited.add(other['id'])
                queue.append(other)
        groups.append(component)

    group_map = {}
    for group in groups:
        distinct_users = len({g.get('user_id') for g in group if g.get('user_id') is not None}) or 1
        representative = max(
            group,
            key=lambda x: (
                x.get('created_at') or datetime.min,
                x.get('id') or 0
            )
        )
        target_status = representative.get('status') or ''
        target_reject_reason = representative.get('reject_reason') or ''
        for member in group:
            status = member.get('status') or ''
            created_at = member.get('created_at')
            urgent_reasons = []
            repeat_count = max(0, distinct_users - 1)
            if safe_float(member.get('risk_score')) >= 80:
                urgent_reasons.append('고위험')
            if repeat_count >= 2:
                urgent_reasons.append('반복 제보')
            if created_at and status in ('접수완료', '관리자 확인중') and (now - created_at).total_seconds() >= GROUP_WINDOW_SEC:
                urgent_reasons.append('처리 지연')
            member['group_reporter_count'] = repeat_count
            member['urgent_reason'] = ', '.join(urgent_reasons)
            member['priority_score'] = priority_score(member, now)
            group_map[member['id']] = {
                'group_ids': [g['id'] for g in group],
                'representative_id': representative.get('id'),
                'group_reporter_count': repeat_count,
                'urgent_reason': member['urgent_reason'],
                'status': target_status,
                'reject_reason': target_reject_reason,
            }
    return group_map


def is_pending(item):
    return (item.get('status') or '') in ('관리자 확인중', '접수완료')


def is_long_pending(item, now=None):
    if now is None:
        now = datetime.now()
    created_at = item.get('created_at')
    return is_pending(item) and created_at and (now - created_at).total_seconds() >= GROUP_WINDOW_SEC


def is_urgent(item, now=None):
    if now is None:
        now = datetime.now()
    return is_pending(item) and (
        safe_float(item.get('risk_score')) >= 80
        or safe_int(item.get('group_reporter_count'), 0) >= 2
        or is_long_pending(item, now)
    )


def summarize_dashboard(reports, now=None):
    """대시보드 상단 요약 카운트를 계산한다."""
    if now is None:
        now = datetime.now()
    today = now.date()
    return {
        'urgent_count': sum(1 for item in reports if is_urgent(item, now)),
        'today_count': sum(1 for item in reports if item.get('created_at') and item['created_at'].date() == today),
        'pending_count': sum(1 for item in reports if is_pending(item)),
        'processing_count': sum(1 for item in reports if (item.get('status') or '') == '처리중'),
        'rejected_count': sum(1 for item in reports if (item.get('status') or '') == '반려'),
    }


def compute_member_stats(member_reports, now=None):
    """회원 상세 페이지의 신고 통계 블록을 계산한다."""
    if now is None:
        now = datetime.now()
    total = len(member_reports)
    received = sum(1 for r in member_reports if (r.get('status') or '') == '접수완료')
    processing = sum(1 for r in member_reports if (r.get('status') or '') == '처리중')
    completed = sum(1 for r in member_reports if (r.get('status') or '') == '처리완료')
    rejected = sum(1 for r in member_reports if (r.get('status') or '') == '반려')
    pending = sum(1 for r in member_reports if (r.get('status') or '') in ('관리자 확인중', '접수완료', '처리중'))
    high_risk_pending = sum(1 for r in member_reports if (r.get('status') or '') in ('관리자 확인중', '접수완료', '처리중') and safe_float(r.get('risk_score')) >= 80)
    long_pending = sum(1 for r in member_reports if (r.get('status') or '') in ('관리자 확인중', '접수완료') and r.get('created_at') and (now - r['created_at']).total_seconds() >= GROUP_WINDOW_SEC)
    recent_7d = sum(1 for r in member_reports if r.get('created_at') and (now - r['created_at']).days < 7)
    recent_30d = sum(1 for r in member_reports if r.get('created_at') and (now - r['created_at']).days < 30)
    approved_rate = round((completed / total) * 100, 1) if total else 0
    rejected_rate = round((rejected / total) * 100, 1) if total else 0
    duplicate_count = sum(1 for r in member_reports if safe_int(r.get('group_reporter_count'), 0) >= 1)
    duplicate_rate = round((duplicate_count / total) * 100, 1) if total else 0

    return {
        'total_reports': total,
        'received_reports': received,
        'processing_reports': processing,
        'completed_reports': completed,
        'rejected_reports': rejected,
        'pending_reports': pending,
        'high_risk_pending_reports': high_risk_pending,
        'long_pending_reports': long_pending,
        'recent_7d_reports': recent_7d,
        'recent_30d_reports': recent_30d,
        'approved_rate': approved_rate,
        'rejected_rate': rejected_rate,
        'duplicate_rate': duplicate_rate,
    }


def member_summary_comment(stats):
    """회원 통계로부터 요약 코멘트 문자열을 생성한다."""
    summary_parts = []
    if stats['recent_30d_reports'] >= 5:
        summary_parts.append('최근 30일 활동 많음')
    if stats['rejected_rate'] >= 40:
        summary_parts.append('반려 비율 높음')
    if stats['duplicate_rate'] <= 20 and stats['total_reports'] > 0:
        summary_parts.append('중복 신고 낮음')
    if not summary_parts:
        summary_parts.append('기본 활동 상태')
    return ' · '.join(summary_parts)


def add_to_region_tree(tree: dict, parts: list):
    """행정구역 계층 리스트를 중첩 dict 트리에 카운트로 누적한다."""
    if not parts:
        return

    node = tree
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1

        if is_last:
            node[part] = node.get(part, 0) + 1
        else:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]


def build_period_bundle(reports, days, now=None):
    """최근 N일 및 직전 동일 기간의 일별 신고 건수 추이를 계산한다."""
    if now is None:
        now = datetime.now()
    labels = []
    values = []
    previous_values = []

    current_start = (now - timedelta(days=days - 1)).date()
    current_dates = [current_start + timedelta(days=i) for i in range(days)]

    prev_start = current_start - timedelta(days=days)
    prev_dates = [prev_start + timedelta(days=i) for i in range(days)]

    current_map = {d: 0 for d in current_dates}
    prev_map = {d: 0 for d in prev_dates}

    for r in reports:
        created_at = r.get('created_at')
        if not created_at:
            continue
        d = created_at.date()
        if d in current_map:
            current_map[d] += 1
        if d in prev_map:
            prev_map[d] += 1

    for d in current_dates:
        labels.append(d.strftime('%m/%d'))
        values.append(current_map[d])

    for d in prev_dates:
        previous_values.append(prev_map[d])

    return {
        "labels": labels,
        "values": values,
        "previous_values": previous_values
    }


def build_all_trend(reports):
    """첫 신고일부터 마지막 신고일까지 전체 일별 추이를 계산한다."""
    dated_reports = [r for r in reports if r.get('created_at')]
    dated_reports.sort(key=lambda x: x.get('created_at') or datetime.min)

    if not dated_reports:
        return {"labels": [], "values": [], "previous_values": []}

    first_date = dated_reports[0]['created_at'].date()
    last_date = dated_reports[-1]['created_at'].date()

    all_dates = []
    cursor = first_date
    while cursor <= last_date:
        all_dates.append(cursor)
        cursor += timedelta(days=1)

    all_map = {d: 0 for d in all_dates}
    for r in dated_reports:
        all_map[r['created_at'].date()] += 1

    return {
        "labels": [d.strftime('%m/%d') for d in all_dates],
        "values": [all_map[d] for d in all_dates],
        "previous_values": []
    }


def paginate(items, page, per_page):
    """리스트를 페이지네이션하여 (해당 페이지 항목, 보정된 page, total_pages, total_count)를 반환한다."""
    import math
    total_count = len(items)
    total_pages = max(1, math.ceil(total_count / per_page))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    return items[start:start + per_page], page, total_pages, total_count
