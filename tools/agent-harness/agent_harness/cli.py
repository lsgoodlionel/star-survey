"""Chinese CLI with one-object JSON and stable process exit codes."""

import argparse
from dataclasses import fields, is_dataclass
from datetime import datetime
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
    for command in ("doctor", "init", "status", "next", "gate", "run-codex", "record-decision", "record-review", "pause", "resume", "finalize"):
        child = sub.add_parser(command)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--repo", type=Path, default=argparse.SUPPRESS)
        if command not in ("doctor", "init"):
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


def main(argv: Sequence[str] | None = None) -> int:
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        try:
            args = _parser().parse_args(arguments)
        except SystemExit as error:
            return int(error.code)
        root = args.repo or _root()
        if args.command == "doctor":
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
                if args.command == "status":
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
