#!/usr/bin/env bash
# 敏感文档加解密（仓库公开，但部分证据类文档不适合明文公开）。
#
#   secure-docs.sh lock     # 加密 sensitive.txt 列出的文档，删除明文
#   secure-docs.sh unlock   # 解密回明文（本地查看/编辑）
#   secure-docs.sh status   # 查看每份文档当前是明文还是密文
#
# 密钥从 STAR_DOCS_KEY_FILE 指向的文件读取（默认 ~/Develop/star-docs.key）。
# 密钥文件绝不能进仓库；明文 .md 由 .gitignore 挡住，只有 .md.gpg 会被提交。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MANIFEST="$(dirname "${BASH_SOURCE[0]}")/sensitive.txt"
KEY_FILE="${STAR_DOCS_KEY_FILE:-$HOME/Develop/star-docs.key}"
CIPHER=AES256

usage() {
  echo "usage: secure-docs.sh {lock|unlock|status}" >&2
  exit 2
}

require_key() {
  if [[ ! -r "$KEY_FILE" ]]; then
    echo "密钥文件不可读：$KEY_FILE（设置 STAR_DOCS_KEY_FILE 指向正确位置）" >&2
    exit 1
  fi
}

documents() {
  grep -vE '^\s*(#|$)' "$MANIFEST"
}

lock() {
  require_key
  local count=0
  while read -r relative; do
    local plain="$REPO_ROOT/$relative"
    [[ -f "$plain" ]] || continue
    gpg --batch --yes --quiet --symmetric --cipher-algo "$CIPHER" \
      --passphrase-file "$KEY_FILE" --output "$plain.gpg" "$plain"
    rm -f "$plain"
    count=$((count + 1))
  done < <(documents)
  echo "locked $count document(s)"
}

unlock() {
  require_key
  local count=0
  while read -r relative; do
    local cipher="$REPO_ROOT/$relative.gpg"
    [[ -f "$cipher" ]] || continue
    gpg --batch --yes --quiet --decrypt --passphrase-file "$KEY_FILE" \
      --output "$REPO_ROOT/$relative" "$cipher"
    count=$((count + 1))
  done < <(documents)
  echo "unlocked $count document(s)"
}

status() {
  while read -r relative; do
    local state="missing"
    [[ -f "$REPO_ROOT/$relative.gpg" ]] && state="encrypted"
    [[ -f "$REPO_ROOT/$relative" ]] && state="plaintext"
    [[ -f "$REPO_ROOT/$relative" && -f "$REPO_ROOT/$relative.gpg" ]] && state="both (run lock)"
    printf '%-50s %s\n' "$relative" "$state"
  done < <(documents)
}

case "${1:-}" in
  lock) lock ;;
  unlock) unlock ;;
  status) status ;;
  *) usage ;;
esac
