"""Chinese CLI with one-object JSON and stable process exit codes."""

import argparse
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import subprocess
from collections.abc import Mapping
from typing import Sequence

from .diagnostics import redact_text
from .doctor import DoctorReport, run_doctor
from .codex_adapter import controlled_tool_errors, controlled_tool_path, run_with_controlled_tools
from .run_service import RunService, ServiceError
from .state import RunState, RunStatus
from .token_report import collect_token_usage, TokenReport, TokenUsage
from .memory_projection import (apply_memory_sync, configure_memory,
                                load_projection_config, memory_status,
                                plan_memory_sync, render_projection)
from .global_policy import install_global_policy


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ServiceError("参数无效；使用 --help 查看用法")


def _json_value(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        def camel(name):
            first, *rest = name.split("_")
            return first + "".join(part.title() for part in rest)
        return {camel(f.name): _json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) or redact_text(k) != k for k in value):
            raise ServiceError("输出包含不安全标识")
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (Path, str)):
        return redact_text(str(value))
    return value


def _parser():
    parser = Parser(prog="agent-harness", description="自治工程运行与验证")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for command in ("doctor", "init", "status", "next", "gate", "run-codex", "record-decision", "record-review", "pause", "resume", "finalize", "token-report", "configure-memory", "memory-status", "sync-memory", "install-global-policy"):
        child = sub.add_parser(command)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--repo", type=Path, default=argparse.SUPPRESS)
        if command not in ("doctor", "init", "token-report", "configure-memory", "memory-status", "install-global-policy"):
            child.add_argument("--run-id", "--run", dest="run_id")
        if command == "init":
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--milestone", required=True)
        elif command == "gate":
            child.add_argument("--extra-gate", action="append", default=[])
            child.add_argument("--profile", action="append", default=[])
        elif command == "run-codex":
            child.add_argument("--max-cycles", type=int, default=5)
        elif command == "record-decision":
            child.add_argument("--type", required=True)
            child.add_argument("--summary", required=True)
        elif command == "record-review":
            child.add_argument("--reviewer", required=True)
            child.add_argument("--report", type=Path, required=True)
        elif command == "pause":
            child.add_argument("--reason", required=True)
        elif command == "token-report":
            child.add_argument("--since", required=True)
            child.add_argument("--until", required=True)
            child.add_argument("--format", choices=("json", "text"), default="text")
            child.add_argument("--session-root", type=Path, action="append", default=[])
        elif command == "configure-memory":
            child.add_argument("--vault", type=Path, required=True)
        elif command == "install-global-policy":
            child.add_argument("--codex-home", type=Path, required=True)
            child.add_argument("--dry-run", action="store_true")
    return parser


def _root():
    current = Path.cwd().resolve(strict=True)
    git = controlled_tool_path(current, "git")
    environment = {"PATH": str(git.parent), "LC_ALL": "C"}
    result = subprocess.run([str(git), "--no-optional-locks", "rev-parse", "--show-toplevel"],
                            text=True, capture_output=True, check=False, env=environment)
    if result.returncode:
        raise ServiceError("当前目录不在 Git 仓库内")
    return Path(result.stdout.strip())


def _rfc3339(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ServiceError("时间必须是带时区的 RFC3339") from None
    if result.utcoffset() is None:
        raise ServiceError("时间必须是带时区的 RFC3339")
    return result.astimezone(timezone.utc)


def main(argv: Sequence[str] | None = None) -> int:
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        try:
            args = _parser().parse_args(arguments)
        except SystemExit as error:
            return int(error.code)
        if args.command == "token-report" and args.format == "json":
            json_output = True
        root = args.repo or _root()
        if args.command == "install-global-policy":
            result = install_global_policy(root, args.codex_home,
                                           dry_run=args.dry_run)
            code = 0
        elif args.command == "configure-memory":
            host_path = configure_memory(root, args.vault)
            config = load_projection_config(root)
            result = {"configured": host_path.is_file(),
                      "vaultAvailable": config.vault_root.is_dir()}
            code = 0
        elif args.command == "memory-status":
            result = memory_status(load_projection_config(root))
            code = 0
        elif args.command == "token-report":
            roots = tuple(args.session_root) or tuple(
                path for path in (Path.home() / ".codex/sessions",
                                  Path.home() / ".codex/archived_sessions")
                if path.is_dir()
            )
            if not roots:
                raise ServiceError("未找到 Codex 会话目录")
            result = collect_token_usage(
                roots, since=_rfc3339(args.since), until=_rfc3339(args.until),
                project_root=root,
            )
            code = 0
        elif args.command == "doctor":
            policy_errors = controlled_tool_errors(root, required_tools=("git",),
                                                    require_codex=False)
            result = (DoctorReport((), (), policy_errors) if policy_errors else
                      run_with_controlled_tools(root, run_doctor, required_tools=("git",),
                                                require_codex=False))
            code = 2 if result.error else 0
        else:
            service = RunService(root)
            if args.command == "init":
                result = service.init(args.plan, args.milestone)
                code = 0
            else:
                run_id = args.run_id or service.select_run()
                code = 0
                if args.command == "sync-memory":
                    state = service.status(run_id)
                    host = json.loads((root / "docs/agent/HARNESS_HOST.json")
                                      .read_text(encoding="utf-8"))
                    manifest = host.get("deliveryManifest")
                    relative = Path(manifest) if isinstance(manifest, str) else Path()
                    if (not manifest or relative.is_absolute() or ".." in relative.parts
                            or "\\" in manifest):
                        raise ServiceError("交付清单路径无效")
                    delivery = json.loads((root / relative).read_text(encoding="utf-8"))
                    roots = tuple(path for path in (
                        Path.home() / ".codex/sessions",
                        Path.home() / ".codex/archived_sessions",
                    ) if path.is_dir())
                    if roots:
                        tokens = collect_token_usage(
                            roots, since=state.created_at,
                            until=datetime.now(timezone.utc), project_root=root)
                    else:
                        tokens = TokenReport(
                            state.created_at, datetime.now(timezone.utc),
                            state.plan_path.as_posix(), 0, 0, 0,
                            TokenUsage(), TokenUsage())
                    projection = render_projection(state, delivery, tokens)
                    result = apply_memory_sync(plan_memory_sync(
                        load_projection_config(root), projection))
                elif args.command == "status":
                    result = service.status(run_id)
                elif args.command == "next":
                    result = service.next_action(run_id)
                elif args.command == "gate":
                    extras = list(args.extra_gate)
                    matrix, _ = service._configs()
                    for profile in args.profile:
                        if profile not in matrix.profiles:
                            raise ServiceError("质量门 profile 无效")
                        extras.extend(matrix.profiles[profile])
                    result = service.run_gates(run_id, extras)
                elif args.command == "record-decision":
                    result = service.record_decision(run_id, args.type, args.summary)
                elif args.command == "run-codex":
                    result = service.run_autonomous(run_id, args.max_cycles)
                    if result.status in (RunStatus.PAUSED, RunStatus.BLOCKED):
                        code = 5
                elif args.command == "record-review":
                    result = service.record_review(run_id, args.reviewer, args.report)
                elif args.command == "pause":
                    result = {"historyPath": str(service.pause(run_id, args.reason)), "status": "paused"}
                    code = 5
                elif args.command == "resume":
                    result = service.resume(run_id)
                else:
                    result = {"historyPath": str(service.finalize(run_id)), "status": "completed"}
                if args.command in ("status", "next") and service.status(run_id).status in (RunStatus.PAUSED, RunStatus.BLOCKED):
                    code = 5
        if json_output:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        elif args.command == "doctor":
            print(f"环境检查：通过 {len(result.ok)}，提醒 {len(result.warning)}，错误 {len(result.error)}")
            for item in (*result.warning, *result.error):
                print(item["id"] + "：" + item["message"])
        elif args.command == "token-report" and isinstance(result, TokenReport):
            print("Token 使用：raw {0}，cached {1}，active {2}；会话 main {3} / subagent {4} / review {5}".format(
                result.usage.raw_tokens, result.usage.cached_input_tokens,
                result.usage.active_tokens, result.main_sessions,
                result.subagent_sessions, result.reviewer_sessions,
            ))
        elif args.command in ("configure-memory", "memory-status"):
            print("记忆投影：" + ("可用" if result["vaultAvailable"] else "等待 vault"))
        elif args.command == "sync-memory":
            print(f"记忆同步：更新 {len(result.updated)}，待重放 {len(result.pending)}")
        elif args.command == "install-global-policy":
            print(f"全局策略：{len(result.changed)} 个文件待更新" if result.dry_run
                  else f"全局策略：已更新 {len(result.changed)} 个文件")
        else:
            messages = {"develop": "开发当前 Milestone", "repair": "修复质量门失败", "gate": "运行质量门",
                        "finalize": "完成收尾", "resume": "检查并恢复运行", "human_review": "需要人工处理", "done": "运行已完成",
                        "record_review": "录入当前 HEAD 的独立审查证据"}
            if args.command == "next":
                print(messages[result.operation])
            elif isinstance(result, RunState):
                statuses = {"planned": "待执行", "active": "执行中", "verifying": "验证中", "repairing": "修复中",
                            "completed": "已完成", "paused": "已暂停", "blocked": "已阻塞"}
                print(f"运行 {result.run_id}：{statuses[result.status.value]}；Milestone {result.milestone_id}")
            else:
                print("运行已暂停" if code == 5 else "运行已完成")
                print("历史：" + result["historyPath"])
        return code
    except (OSError, ValueError) as error:
        code = error.exit_code if isinstance(error, ServiceError) else 2
        message = str(error) if isinstance(error, ServiceError) else "输入、配置或本地文件不可用"
        if json_output:
            print(json.dumps({"error": redact_text(message), "exitCode": code}, ensure_ascii=False))
        else:
            print(redact_text(message), file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
