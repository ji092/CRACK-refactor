#!/usr/bin/env bash
# git 훅 설치 스크립트. 새 환경/클론 후 한 번 실행하면 pre-commit 훅이 걸린다.
#   사용법:  bash scripts/install-hooks.sh
set -e
repo_root="$(git rev-parse --show-toplevel)"
cp "$repo_root/scripts/pre-commit" "$repo_root/.git/hooks/pre-commit"
chmod +x "$repo_root/.git/hooks/pre-commit"
echo "[install-hooks] pre-commit 훅 설치 완료 → .git/hooks/pre-commit"
