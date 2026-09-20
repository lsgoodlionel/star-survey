#!/usr/bin/env bash
# P0-00.5 租户隔离验证栈的共享定义。调用方需先设置 REPO_ROOT。
# 提供：COMPOSE、租户常量、prepare_tenancy_stack。

TENANCY_DIR="$REPO_ROOT/platform/deploy/tenancy"
COMPOSE=(docker compose -p survey-tenancy -f "$TENANCY_DIR/docker-compose.tenancy.yml")

# 两个租户的固定参数。PHP 越权脚本里有一份同样的常量（见
# platform/tests/e2e/tenancy_isolation.php 顶部），改动时两处都要改。
TENANTS=(a b)
WEB_A=survey-tenancy-web-a
WEB_B=survey-tenancy-web-b
DB_A=survey-tenancy-db-a
DB_B=survey-tenancy-db-b
ADMIN_USER_A=admin_a
ADMIN_PASSWORD_A=tenant-a-secret
ADMIN_USER_B=admin_b
ADMIN_PASSWORD_B=tenant-b-secret
# 越权脚本用到的夹具：同号问卷、只属于 B 的问卷、B 的一份答卷上传文件。
SHARED_SID=555001
VICTIM_SID=555002
VICTIM_UPLOAD_FILE=fu_tenantbcanary0
VICTIM_UPLOAD_BODY=TENANT-B-CANARY-UPLOAD

# 把一个租户的运行时目录准备好：tmp 与 upload/surveys 都是该租户独占的卷，
# 首次启动时是空的，需要建目录并补回引擎自带的 upload/surveys/.htaccess。
prepare_tenant_runtime() {
  local container="$1"
  docker exec "$container" sh -c '
    set -e
    mkdir -p tmp/runtime tmp/assets tmp/upload upload/surveys
    chmod -R 777 tmp upload/surveys
  '
  docker exec -i "$container" sh -c 'cat > upload/surveys/.htaccess' \
    < "$REPO_ROOT/upload/surveys/.htaccess"
}

# 某个租户库里是否已经装过引擎。
tenant_is_installed() {
  local db_container="$1" tenant="$2"
  docker exec "$db_container" mariadb "-utenant_$tenant" "-ptenant-$tenant-db-pass" -N -e \
    "SELECT COUNT(*) FROM information_schema.tables
     WHERE table_name='lime_users' AND table_schema='tenant_$tenant'"
}

# 打开 RemoteControl（JSON-RPC）。每个实例只在自己的库里改自己的设置。
enable_remote_control() {
  local db_container="$1" tenant="$2"
  docker exec "$db_container" mariadb "-utenant_$tenant" "-ptenant-$tenant-db-pass" "tenant_$tenant" -e "
    DELETE FROM lime_settings_global WHERE stg_name='RPCInterface';
    INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json');"
}

# 在租户 B 的上传目录里放一份“受害者文件”，供越权脚本尝试跨租户读取。
seed_victim_upload() {
  docker exec "$WEB_B" sh -c "
    set -e
    mkdir -p upload/surveys/$VICTIM_SID/files
    printf '%s' '$VICTIM_UPLOAD_BODY' > upload/surveys/$VICTIM_SID/files/$VICTIM_UPLOAD_FILE
    chmod -R 777 upload/surveys"
}

prepare_tenancy_stack() {
  # 配置模板复制一份到 .runtime，避免引擎回写时弄脏仓库里的模板。
  mkdir -p "$TENANCY_DIR/.runtime"
  cp "$TENANCY_DIR/config.tenant-a.php" "$TENANCY_DIR/.runtime/config.tenant-a.php"
  cp "$TENANCY_DIR/config.tenant-b.php" "$TENANCY_DIR/.runtime/config.tenant-b.php"
  cp "$TENANCY_DIR/allowed_hosts.tenant-a.php" "$TENANCY_DIR/.runtime/allowed_hosts.tenant-a.php"
  cp "$TENANCY_DIR/allowed_hosts.tenant-b.php" "$TENANCY_DIR/.runtime/allowed_hosts.tenant-b.php"

  "${COMPOSE[@]}" up -d >&2
  for db in "$DB_A" "$DB_B"; do
    until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$db")" == "healthy" ]]; do
      sleep 2
    done
  done

  prepare_tenant_runtime "$WEB_A"
  prepare_tenant_runtime "$WEB_B"

  if [[ "$(tenant_is_installed "$DB_A" a)" == "0" ]]; then
    echo "安装租户 A 引擎实例..." >&2
    docker exec "$WEB_A" php application/commands/console.php install \
      "$ADMIN_USER_A" "$ADMIN_PASSWORD_A" "Tenant A Admin" "admin-a@tenant-a.invalid"
  fi
  if [[ "$(tenant_is_installed "$DB_B" b)" == "0" ]]; then
    echo "安装租户 B 引擎实例..." >&2
    docker exec "$WEB_B" php application/commands/console.php install \
      "$ADMIN_USER_B" "$ADMIN_PASSWORD_B" "Tenant B Admin" "admin-b@tenant-b.invalid"
  fi

  enable_remote_control "$DB_A" a
  enable_remote_control "$DB_B" b
  seed_victim_upload
}
