#!/usr/bin/env bash
# 一键发布：创建 GitHub 仓库 → 推送 → 开启 Pages → 写入 TMDB Key → 触发首次构建
# 用法： bash push.sh [仓库名] [public|private]
set -euo pipefail
REPO_NAME="${1:-emos-watch-dynamic}"
VISIBILITY="${2:-public}"
cd "$(dirname "$0")"

command -v gh >/dev/null 2>&1 || { echo "请先安装 GitHub CLI：https://cli.github.com/"; exit 1; }
gh auth status >/dev/null 2>&1 || gh auth login

if [ ! -d .git ]; then
  git init -b main >/dev/null
fi
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-emos-watch-bot}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-bot@users.noreply.github.com}" \
    commit -m "feat: EMOS 动态片单生成器（GitHub Actions + Pages）" >/dev/null 2>&1 || true

OWNER="$(gh api user -q .login)"

if gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
  echo "仓库已存在：$OWNER/$REPO_NAME，直接推送"
  git remote get-url origin >/dev/null 2>&1 || \
    git remote add origin "https://github.com/$OWNER/$REPO_NAME.git"
  git push -u origin main
else
  gh repo create "$REPO_NAME" --"$VISIBILITY" --source=. --remote=origin --push
fi

echo "开启 GitHub Pages（Actions 构建模式）…"
PAGES_JSON='{"build_type":"workflow"}'
if printf '%s' "$PAGES_JSON" | gh api -X POST "repos/$OWNER/$REPO_NAME/pages" --input - >/dev/null 2>&1; then
  echo "Pages 已创建"
elif printf '%s' "$PAGES_JSON" | gh api -X PUT "repos/$OWNER/$REPO_NAME/pages" --input - >/dev/null 2>&1; then
  echo "Pages 已更新为 Actions 构建"
else
  echo "⚠️ 自动开启 Pages 失败：请到 Settings → Pages → Source 选择 GitHub Actions 后重跑一次 Actions"
fi

if [ -n "${TMDB_API_KEY:-}" ]; then
  printf '%s' "$TMDB_API_KEY" | gh secret set TMDB_API_KEY
else
  read -r -s -p "请输入 TMDB API Key（v3，可留空跳过）： " KEY; echo
  [ -n "$KEY" ] && printf '%s' "$KEY" | gh secret set TMDB_API_KEY
fi

echo "触发首次构建…"
gh workflow run update.yml
sleep 3
gh run list --limit 1

echo ""
echo "仓库：https://github.com/$OWNER/$REPO_NAME"
echo "接口：https://$OWNER.github.io/$REPO_NAME/watch/tmdb-daily.json"
