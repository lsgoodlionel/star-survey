#!/usr/bin/env bash
# P0-00.7 考试与配额验证栈的共享定义。调用方需先设置 REPO_ROOT。
# 提供：COMPOSE、EXAM_WEB、EXAM_DB、管理员常量、prepare_exam_stack。

EXAM_DIR="$REPO_ROOT/platform/deploy/exam"
COMPOSE=(docker compose -p survey-exam -f "$EXAM_DIR/docker-compose.exam.yml")

EXAM_WEB=survey-exam-web
EXAM_DB=survey-exam-db
EXAM_DB_USER=exam
EXAM_DB_PASSWORD=exam-db-pass
EXAM_DB_NAME=exam
ADMIN_USER=admin
ADMIN_PASSWORD=exam-secret

# 本栈的 tmp 与 upload/surveys 都是独占卷，首次启动时是空的：
# 需要建目录，并补回引擎自带的 upload/surveys/.htaccess。
prepare_exam_runtime() {
  docker exec "$EXAM_WEB" sh -c '
    set -e
    mkdir -p tmp/runtime tmp/assets tmp/upload upload/surveys
    chmod -R 777 tmp upload/surveys
  '
  docker exec -i "$EXAM_WEB" sh -c 'cat > upload/surveys/.htaccess' \
    < "$REPO_ROOT/upload/surveys/.htaccess"
}

exam_is_installed() {
  docker exec "$EXAM_DB" mariadb "-u$EXAM_DB_USER" "-p$EXAM_DB_PASSWORD" -N -e \
    "SELECT COUNT(*) FROM information_schema.tables
     WHERE table_name='lime_users' AND table_schema='$EXAM_DB_NAME'"
}

# 打开 RemoteControl（JSON-RPC），端到端脚本用它建问卷与配额。
enable_remote_control() {
  docker exec "$EXAM_DB" mariadb "-u$EXAM_DB_USER" "-p$EXAM_DB_PASSWORD" "$EXAM_DB_NAME" -e "
    DELETE FROM lime_settings_global WHERE stg_name='RPCInterface';
    INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json');"
}

prepare_exam_stack() {
  mkdir -p "$EXAM_DIR/.runtime"
  cp "$EXAM_DIR/config.exam.php" "$EXAM_DIR/.runtime/config.php"
  cp "$EXAM_DIR/allowed_hosts.exam.php" "$EXAM_DIR/.runtime/allowed_hosts.php"

  "${COMPOSE[@]}" up -d >&2
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$EXAM_DB")" == "healthy" ]]; do
    sleep 2
  done

  prepare_exam_runtime

  if [[ "$(exam_is_installed)" == "0" ]]; then
    echo "安装考试验证栈的引擎实例..." >&2
    docker exec "$EXAM_WEB" php application/commands/console.php install \
      "$ADMIN_USER" "$ADMIN_PASSWORD" "Exam Admin" "admin@exam.invalid"
  fi

  enable_remote_control
}
