#!/usr/bin/env bash
# =============================================================================
# 커밋 전 시크릿 자동 탐지 스캐너
# pre-commit 훅에서 호출된다. 스테이징된 내용만 검사한다.
# 문제가 발견되면 0이 아닌 코드로 종료하여 커밋을 막는다.
#
# 우회(정말 필요할 때만): git commit --no-verify
# =============================================================================
set -u

RED=$'\033[0;31m'; YEL=$'\033[0;33m'; NC=$'\033[0m'
violations=0

# 스테이징된(추가/수정/복사) 파일 목록
mapfile -t files < <(git diff --cached --name-only --diff-filter=ACM)
[ ${#files[@]} -eq 0 ] && exit 0

# ---------------------------------------------------------------------------
# 1) 애초에 커밋되면 안 되는 "경로" 차단 (gitignore를 강제로 뚫고 add한 경우 대비)
# ---------------------------------------------------------------------------
path_block_regex='\.env($|\.)|(^|/)secrets/|(^|/)db/(security_viewer_setup|app_account_least_privilege)\.sql$|(^|/)db/DB_SECURITY_DESIGN\.md$|\.pem$|\.key$|id_rsa'

for f in "${files[@]}"; do
    if printf '%s' "$f" | grep -qiE "$path_block_regex"; then
        echo "${RED}[차단] 민감 파일 경로가 스테이징됨:${NC} $f"
        violations=$((violations+1))
    fi
done

# ---------------------------------------------------------------------------
# 2) 스테이징된 "내용"에서 시크릿 패턴 탐지
#    각 패턴: 설명|정규식
# ---------------------------------------------------------------------------
patterns=(
    "개인키(PEM)|-----BEGIN (RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"
    "AWS 액세스 키|AKIA[0-9A-Z]{16}"
    "GitHub 토큰|gh[pousr]_[A-Za-z0-9]{20,}"
    "Slack 토큰|xox[baprs]-[A-Za-z0-9-]{10,}"
    "Google API 키|AIza[0-9A-Za-z_-]{30,}"
    "SQL 계정 비밀번호|IDENTIFIED BY[[:space:]]*'[^']+'"
    "TiDB 접속 호스트|[a-z0-9.-]+\.tidbcloud\.com"
    "DB 클러스터 접두사|223U129WXYoduGH"
    "환경변수 비밀값 대입|(DB_PASSWORD|DB_USER|FLASK_SECRET_KEY|SECRET_KEY|API_KEY|ACCESS_TOKEN)[[:space:]]*=[[:space:]]*['\"][^'\"[:space:]]{6,}"
    "일반 비밀값 대입|(password|passwd|pwd|secret|token|api[_-]?key)[[:space:]]*[:=][[:space:]]*['\"][^'\"[:space:]]{8,}['\"]"
)

for f in "${files[@]}"; do
    # 바이너리/이미지/모델은 건너뜀
    case "$f" in
        *.png|*.jpg|*.jpeg|*.gif|*.ico|*.pt|*.zip|*.7z|*.rar|*.pdf|*.mp4|*.mov) continue ;;
        # 스캐너 자신은 탐지 패턴을 문자열로 포함하므로 내용 검사에서 제외 (자기참조 오탐 방지)
        scripts/secret-scan.sh) continue ;;
    esac
    blob="$(git show ":$f" 2>/dev/null)" || continue
    for entry in "${patterns[@]}"; do
        desc="${entry%%|*}"; rx="${entry#*|}"
        hits="$(printf '%s' "$blob" | grep -nEi "$rx" 2>/dev/null | head -3)"
        if [ -n "$hits" ]; then
            echo "${RED}[차단] ${desc}${NC} → ${f}"
            echo "$hits" | sed 's/^/        /'
            violations=$((violations+1))
        fi
    done
done

if [ "$violations" -gt 0 ]; then
    echo ""
    echo "${RED}커밋 중단: 시크릿 의심 ${violations}건 발견.${NC}"
    echo "${YEL}오탐이 확실하면 'git commit --no-verify' 로 우회할 수 있으나, 반드시 직접 확인 후 사용하십시오.${NC}"
    exit 1
fi
exit 0
