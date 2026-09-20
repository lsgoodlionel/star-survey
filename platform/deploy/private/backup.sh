#!/usr/bin/env bash
# P0-00.8：对一个运行中的私有化实例做全量备份（数据库 ＋ upload/ ＋ 实例配置）。
#
# 用法：
#   platform/deploy/private/backup.sh [输出目录]
# 默认输出目录：platform/deploy/private/.runtime/backup-<时间戳>
#
# 备份件构成（三件套 ＋ 清单）：
#   db.sql.gz       mariadb-dump --single-transaction 的整库逻辑备份
#   upload.tar.gz   upload/（答卷附件、主题、插件资源）
#   config.tar.gz   application/config/{config,security,allowed_hosts}.php
#   manifest.txt    引擎版本、DB 版本、表数、问卷数、答卷数、各件大小与 SHA-256
#
# security.php 里是 sodium 加密密钥：丢了它，库里的加密字段就再也解不开。
# 所以它必须进备份件，也因此备份件本身必须按密文对待。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/private/lib.sh"

OUT_DIR="${1:-$PRIVATE_DIR/.runtime/backup-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT_DIR"

if ! docker inspect "$WEB" >/dev/null 2>&1; then
  echo "实例 $WEB 不存在，先跑 setup.sh。" >&2
  exit 1
fi
if [[ "$(engine_is_installed)" != "1" ]]; then
  echo "库 $DB_NAME 里没有 lime_users，实例尚未安装，无可备份内容。" >&2
  exit 1
fi

started_at=$(date +%s)

echo "[1/3] 导出数据库 $DB_NAME ..." >&2
# --single-transaction：InnoDB 下拿一致性快照且不锁表；避免备份期间阻塞填写。
docker exec "$DB" mariadb-dump \
  "-u$DB_USER" "-p$DB_PASSWORD" \
  --single-transaction --quick --default-character-set=utf8mb4 \
  --routines --events --triggers \
  "$DB_NAME" | gzip -6 > "$OUT_DIR/$BACKUP_DB_FILE"

echo "[2/3] 打包 upload/ ..." >&2
docker exec "$WEB" tar -cf - -C /var/www/html upload | gzip -6 > "$OUT_DIR/$BACKUP_UPLOAD_FILE"

echo "[3/3] 打包实例配置 ..." >&2
# security.php 由引擎首次运行时生成，可能不存在；用 -f 逐个判断避免 tar 报错。
docker exec "$WEB" sh -c '
  cd /var/www/html/application/config
  files=""
  for f in config.php security.php allowed_hosts.php; do
    [ -f "$f" ] && files="$files $f"
  done
  # shellcheck disable=SC2086
  tar -cf - $files
' | gzip -6 > "$OUT_DIR/$BACKUP_CONFIG_FILE"

finished_at=$(date +%s)

# ---- 清单：恢复后逐项核对的依据 ----------------------------------------
sql_count() {
  docker exec "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" -N -e "$1" "$DB_NAME" 2>/dev/null \
    | tr -d '[:space:]'
}
table_count=$(sql_count "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'")
survey_count=$(sql_count "SELECT COUNT(*) FROM lime_surveys")
dbversion=$(sql_count "SELECT stg_value FROM lime_settings_global WHERE stg_name='DBVersion'")
response_rows=$(docker exec "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" -N -e "
  SELECT GROUP_CONCAT(CONCAT(table_name,'=',table_rows) ORDER BY table_name SEPARATOR ' ')
  FROM information_schema.tables
  WHERE table_schema='$DB_NAME' AND table_name LIKE 'lime_responses\_%'" "$DB_NAME" 2>/dev/null)

{
  echo "# 私有化实例备份清单"
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "elapsed_seconds=$((finished_at - started_at))"
  echo "source_container=$WEB"
  echo "source_database=$DB_NAME"
  echo "engine_dbversion=$dbversion"
  echo "table_count=$table_count"
  echo "survey_count=$survey_count"
  echo "response_tables=$response_rows"
  echo "# 件大小与校验"
  for f in "$BACKUP_DB_FILE" "$BACKUP_UPLOAD_FILE" "$BACKUP_CONFIG_FILE"; do
    size=$(wc -c < "$OUT_DIR/$f" | tr -d '[:space:]')
    sum=$(shasum -a 256 "$OUT_DIR/$f" | awk '{print $1}')
    echo "$f size_bytes=$size sha256=$sum"
  done
} > "$OUT_DIR/$BACKUP_MANIFEST_FILE"

echo "备份完成：$OUT_DIR （耗时 $((finished_at - started_at)) 秒）"
cat "$OUT_DIR/$BACKUP_MANIFEST_FILE"
