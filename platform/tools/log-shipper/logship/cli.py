"""命令行入口。

    logship ship   --config c.json --summary "错误摘要"   # 立即上报一次
    logship drain  --config c.json                        # 处理应用写入 spool 的故障
    logship doctor --config c.json                        # 体检：配置、来源、令牌

退出码：0 正常；1 体检不通过或上报失败；2 配置或令牌问题。
应用侧只需往 spool 目录写一个 JSON（至少含 errorSummary），采集、脱敏、限流、
上传都由本工具负责，避免把 GitHub 令牌散布到各个应用进程里。
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .bundle import BundleBuilder, IncidentContext
from .config import ConfigError, ShipperConfig
from .dedupe import ShipGate
from .github import GitHubUploader, UploadError
from .redact import Redactor
from .sources import collect

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_CONFIG = 2


def run(argv: List[str], env: Optional[Dict[str, str]] = None) -> int:
    env = os.environ if env is None else env
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = ShipperConfig.load(Path(args.config))
    except ConfigError as error:
        _report(f"config error: {error}")
        return _EXIT_CONFIG

    if args.command == "doctor":
        return _doctor(config, env)
    if args.command == "ship":
        return _ship_once(config, env, args.summary, args.dry_run)
    return _drain(config, env, args.dry_run)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="logship", description="服务器日志采集与故障上报")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("ship", "drain", "doctor"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--config", required=True)
        if name != "doctor":
            subparser.add_argument("--dry-run", action="store_true")
        if name == "ship":
            subparser.add_argument("--summary", required=True, help="错误摘要，用于去重指纹与提交信息")
    return parser


def _doctor(config: ShipperConfig, env: Dict[str, str]) -> int:
    problems = []
    if not env.get(config.token_env):
        problems.append(f"environment variable {config.token_env} is empty")
    collected = collect(config.sources)
    for name, text in collected.items():
        if text.startswith("[logship] source unavailable"):
            problems.append(f"source {name}: {text.strip()}")
    for directory in (config.state_dir, config.spool_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            problems.append(f"cannot create {directory}: {error}")

    for problem in problems:
        _report(f"FAIL {problem}")
    if problems:
        return _EXIT_FAILED
    _report(f"OK {config.app}/{config.instance} -> {config.repository} ({len(collected)} sources)")
    return _EXIT_OK


def _ship_once(config: ShipperConfig, env: Dict[str, str], summary: str, dry_run: bool) -> int:
    token = env.get(config.token_env, "")
    if not token and not dry_run:
        _report(f"config error: environment variable {config.token_env} is empty")
        return _EXIT_CONFIG
    return _ship(config, token, summary, dry_run)


def _drain(config: ShipperConfig, env: Dict[str, str], dry_run: bool) -> int:
    token = env.get(config.token_env, "")
    if not token and not dry_run:
        _report(f"config error: environment variable {config.token_env} is empty")
        return _EXIT_CONFIG

    incidents = sorted(config.spool_dir.glob("*.json")) if config.spool_dir.exists() else []
    if not incidents:
        _report("no spooled incidents")
        return _EXIT_OK

    archive = config.spool_dir / "shipped"
    archive.mkdir(parents=True, exist_ok=True)
    failures = 0
    for incident in incidents:
        summary = _summary_of(incident)
        result = _ship(config, token, summary, dry_run)
        if result != _EXIT_OK:
            failures += 1
            continue
        # 归档而不是删除：上传失败时证据要留在机器上。
        shutil.move(str(incident), str(archive / incident.name))
    return _EXIT_FAILED if failures else _EXIT_OK


def _summary_of(incident: Path) -> str:
    try:
        payload = json.loads(incident.read_text(encoding="utf-8"))
        return str(payload.get("errorSummary") or incident.stem)
    except (OSError, ValueError):
        return incident.stem


def _ship(config: ShipperConfig, token: str, summary: str, dry_run: bool) -> int:
    context = IncidentContext(
        app=config.app,
        instance=config.instance,
        environment=config.environment,
        version=config.version,
        error_summary=summary,
        occurred_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    gate = ShipGate(config.state_dir / "gate.json", config.gate_limits)
    fingerprint = BundleBuilder.fingerprint(context)
    decision = gate.evaluate(fingerprint)
    if not decision.allowed:
        _report(f"skipped ({decision.reason}) fingerprint={fingerprint} suppressed={decision.suppressed_count}")
        return _EXIT_OK

    builder = BundleBuilder(Redactor(config.redaction_rules), config.bundle_limits)
    incident_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{fingerprint}"
    bundle_path = config.state_dir / "bundles" / f"{incident_id}.tar.gz"
    try:
        bundle = builder.build(
            IncidentContext(**{**context.__dict__, "suppressed_count": decision.suppressed_count}),
            collect(config.sources),
            bundle_path,
        )
    except ValueError as error:
        _report(f"bundle rejected: {error}")
        return _EXIT_FAILED

    uploader = GitHubUploader(
        repository=config.repository,
        token=token,
        branch=config.branch,
        dry_run=dry_run,
        client_certificate=config.client_certificate or None,
    )
    try:
        result = uploader.upload(bundle.path, bundle.manifest, app=config.app, incident_id=incident_id)
    except UploadError as error:
        _report(f"upload failed, bundle kept at {bundle.path}: {error}")
        return _EXIT_FAILED

    gate.record(fingerprint)
    if result.uploaded:
        bundle.path.unlink(missing_ok=True)
        _report(f"uploaded {result.directory}")
    else:
        _report(f"dry-run, would upload {result.directory} ({bundle.size_bytes} bytes)")
    return _EXIT_OK


def _report(message: str) -> None:
    print(f"[logship] {message}", file=sys.stderr)


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
