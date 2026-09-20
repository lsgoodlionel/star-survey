#!/usr/bin/env bash
# P0-00.8：把 backup.sh 的备份件恢复进一套全新的私有化实例。
#
# 用法：
#   platform/deploy/private/restore.sh <备份目录>
#
# 前提：目标栈已经起来（setup.sh 会自动跑一次全新安装；本脚本会把那次安装
# 的库整个丢掉，用备份件覆盖）。恢复顺序是有讲究的：
#   1. 先恢复 config.php / security.php —— security.php 是 sodium 密钥，
#      必须早于任何读写加密字段的操作到位；
#   2. 再 DROP/CREATE 库并导入 dump —— 不能直接往已装好的库里灌，
#      否则残留的 lime_* 表会和 dump 里的表冲突；
#   3. 最后覆盖 upload/。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/private/lib.sh"

BACKUP_DIR="${1:-}"
if [[ -z "$BACKUP_DIR" || ! -d "$BACKUP_DIR" ]]; then
  echo "用法：$0 <备份目录>" >&2
  exit 1
fi
for f in "$BACKUP_DB_FILE" "$BACKUP_UPLOAD_FILE" "$BACKUP_CONFIG_FILE" "$BACKUP_MANIFEST_FILE"; do
  if [[ ! -f "$BACKUP_DIR/$f" ]]; then
    echo "备份件缺少 $f，拒绝恢复。" >&2
    exit 1
  fi
done

echo "[0/4] 校验备份件完整性 ..." >&2
while read -r name rest; do
  case "$name" in
    "$BACKUP_DB_FILE"|"$BACKUP_UPLOAD_FILE"|"$BACKUP_CONFIG_FILE") ;;
    *) continue ;;
  esac
  want=$(printf '%s' "$rest" | tr ' ' '\n' | sed -n 's/^sha256=//p')
  got=$(shasum -a 256 "$BACKUP_DIR/$name" | awk '{print $1}')
  if [[ "$want" != "$got" ]]; then
    echo "$name 校验失败：清单 $want，实际 $got" >&2
    exit 1
  fi
done < "$BACKUP_DIR/$BACKUP_MANIFEST_FILE"
echo "      三个备份件 SHA-256 与清单一致。" >&2

if ! docker inspect "$WEB" >/dev/null 2>&1; then
  echo "目标实例 $WEB 不存在，先跑 setup.sh。" >&2
  exit 1
fi
wait_for_db

started_at=$(date +%s)

echo "[1/4] 恢复实例配置（含 security.php 加密密钥）..." >&2
gunzip -c "$BACKUP_DIR/$BACKUP_CONFIG_FILE" \
  | docker exec -i "$WEB" tar -xf - -C /var/www/html/application/config

echo "[2/4] 重建空库并导入 dump ..." >&2
docker exec "$DB" mariadb "-uroot" "-p${PRIVATE_DB_ROOT_PASSWORD:-private-root}" -e \
  "DROP DATABASE IF EXISTS \`$DB_NAME\`;
   CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   GRANT ALL ON \`$DB_NAME\`.* TO '$DB_USER'@'%';"
gunzip -c "$BACKUP_DIR/$BACKUP_DB_FILE" \
  | docker exec -i "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" --default-character-set=utf8mb4 "$DB_NAME"

echo "[3/4] 覆盖 upload/ ..." >&2
docker exec "$WEB" sh -c 'rm -rf /var/www/html/upload/* /var/www/html/upload/.??* 2>/dev/null; true'
gunzip -c "$BACKUP_DIR/$BACKUP_UPLOAD_FILE" \
  | docker exec -i "$WEB" tar -xf - -C /var/www/html
docker exec "$WEB" sh -c 'chown -R www-data:www-data /var/www/html/upload && chmod -R 777 /var/www/html/upload'

echo "[4/4] 清空引擎运行时缓存（schema 缓存会记住旧库结构）..." >&2
docker exec "$WEB" sh -c 'rm -rf /var/www/html/tmp/runtime/* 2>/dev/null; true'

finished_at=$(date +%s)
echo "恢复完成，耗时 $((finished_at - started_at)) 秒。"
echo "清单里记录的源实例状态："
grep -E '^(engine_dbversion|table_count|survey_count|response_tables)=' "$BACKUP_DIR/$BACKUP_MANIFEST_FILE"
