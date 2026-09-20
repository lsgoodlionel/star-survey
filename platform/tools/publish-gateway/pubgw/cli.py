"""命令行入口。

    pubgw validate    --definition survey.json
    pubgw compile     --definition survey.json --out survey.lss
    pubgw publish     --definition survey.json --engine-url http://localhost \
                      --engine-instance survey-test-web --binding-out binding.json
    pubgw drift-check --binding binding.json --engine-url http://localhost

退出码：0 通过；1 发布失败／校验不通过／检出漂移；2 配置或定义本身有问题。

口令只从环境变量读（默认 ``LIMESURVEY_RPC_PASSWORD``），不接受命令行参数：
命令行会进 shell 历史与进程列表。
"""

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from .binding import BindingRecord
from .compiler import CompileError, LssCompiler
from .drift import check_drift
from .fieldmap import parse_fieldmap
from .model import DefinitionError, SurveyDefinition
from .publish import Publisher
from .rpc import HttpTransport, RemoteControlClient, RpcError
from .validate import validate_definition

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

DEFAULT_PASSWORD_ENV = "LIMESURVEY_RPC_PASSWORD"

TransportFactory = Callable[[str], Callable[[bytes], bytes]]


def run(
    argv: List[str],
    env: Optional[Dict[str, str]] = None,
    transport_factory: Optional[TransportFactory] = None,
) -> int:
    env = os.environ if env is None else env
    args = _parser().parse_args(argv)
    handlers = {
        "validate": _validate,
        "compile": _compile,
        "publish": _publish,
        "drift-check": _drift_check,
    }
    try:
        return handlers[args.command](args, env, transport_factory or HttpTransport)
    except DefinitionError as error:
        return _report("definition error: {}".format(error), EXIT_CONFIG)
    except CompileError as error:
        return _report("compile error: {}".format(error), EXIT_CONFIG)
    except OSError as error:
        return _report("io error: {}".format(error), EXIT_CONFIG)
    except RpcError as error:
        return _report(str(error), EXIT_FAILED)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pubgw", description="LimeSurvey 发布网关")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "compile", "publish"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--definition", required=True, help="平台问卷定义（JSON）")
        if name == "compile":
            subparser.add_argument("--out", help="写出 .lss 的路径，缺省写到标准输出")
        if name == "publish":
            _add_engine_arguments(subparser)
            subparser.add_argument("--engine-instance", default="", help="引擎实例标识，写进绑定记录")
            subparser.add_argument("--binding-out", help="绑定记录写出路径")

    drift = subparsers.add_parser("drift-check")
    drift.add_argument("--binding", required=True, help="发布时产出的绑定记录")
    _add_engine_arguments(drift)
    return parser


def _add_engine_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine-url", required=True, help="引擎根地址，例如 http://localhost")
    parser.add_argument("--user", default="admin", help="RemoteControl 账号")
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV, help="存放口令的环境变量名")
    parser.add_argument("--timeout", type=int, default=180, help="单次 RPC 超时秒数")


# ------------------------------------------------------------------ 命令


def _validate(args, env, transport_factory) -> int:
    definition = _load_definition(args.definition)
    report = validate_definition(definition)
    _emit(report.to_dict())
    return EXIT_OK if report.is_valid else EXIT_FAILED


def _compile(args, env, transport_factory) -> int:
    definition = _load_definition(args.definition)
    compiled = LssCompiler().compile(definition)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(compiled.lss)
        _report("compiled {} ({})".format(args.out, compiled.fingerprint), EXIT_OK)
    else:
        sys.stdout.write(compiled.lss)
    return EXIT_OK


def _publish(args, env, transport_factory) -> int:
    definition = _load_definition(args.definition)
    client, failure = _client(args, env, transport_factory)
    if client is None:
        return failure

    try:
        result = Publisher(client, engine_instance=args.engine_instance).publish(definition)
    finally:
        client.logout()

    _emit(result.to_dict())
    if result.binding and args.binding_out:
        _write_json(args.binding_out, result.binding.to_dict())
    if not result.ok:
        for message in result.failures:
            _report(message, EXIT_FAILED)
        if result.orphan_survey_id is not None:
            _report("orphan survey left behind: sid={}".format(result.orphan_survey_id), EXIT_FAILED)
    return EXIT_OK if result.ok else EXIT_FAILED


def _drift_check(args, env, transport_factory) -> int:
    with open(args.binding, encoding="utf-8") as handle:
        record = BindingRecord.from_dict(json.load(handle))
    client, failure = _client(args, env, transport_factory)
    if client is None:
        return failure

    try:
        rows = parse_fieldmap(client.get_fieldmap(record.survey_id))
    finally:
        client.logout()

    report = check_drift(record, rows)
    _emit(report.to_dict())
    for issue in report.issues:
        _report("{} {}".format(issue.code, issue.detail), EXIT_FAILED)
    return EXIT_FAILED if report.drifted else EXIT_OK


# ------------------------------------------------------------------ 零件


def _client(args, env, transport_factory):
    password = env.get(args.password_env, "")
    if not password:
        return None, _report(
            "environment variable {} is empty".format(args.password_env), EXIT_CONFIG
        )
    client = RemoteControlClient(transport_factory(args.engine_url))
    client.login(args.user, password)
    return client, EXIT_OK


def _load_definition(path: str) -> SurveyDefinition:
    with open(path, encoding="utf-8") as handle:
        return SurveyDefinition.from_json(handle.read())


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _report(message: str, code: int) -> int:
    print("[pubgw] {}".format(message), file=sys.stderr)
    return code


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
