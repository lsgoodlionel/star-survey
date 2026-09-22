#!/usr/bin/env python3

"""契约 v1.2（重新发布、收口、漂移检查）对真引擎的宿主机驱动。

由 platform/deploy/test/run-publish-gateway-service.sh 编排，不单独运行。只走网关的 HTTP 接口，
按契约签名；引擎侧的断言（过期列、作答入口、改代码）由编排脚本在两个子命令之间完成。

子命令：

* ``republish``：同一定义换新 requestId 再发布 → 新 sid；收口旧 sid（再收口一次为 alreadyClosed）；
  收口不存在的 sid → 502；新版漂移检查 match；已收口的旧 sid 只比指纹也是 match（过期不算漂移）。
* ``drift``：编排脚本在引擎里改了新版的题目代码之后，漂移检查必须报 drift 并指名被改的题目。

两个子命令都断言没有任何应答带出引擎口令；每一步不符都以非零退出码结束。
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HTTP_TIMEOUT_SECONDS = 300
MISSING_SID = 999_999
DRIFTED_QUESTION = ("QSINGLE", "QDRIFTED")


class Driver:
    def __init__(self, url: str, secret: bytes, password: str, sign) -> None:
        self._url = url.rstrip("/")
        self._secret = secret
        self._password = password.encode("utf-8")
        self._sign = sign
        self.failures: List[str] = []

    def post(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stamp = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-Pubgw-Timestamp": stamp,
                   "X-Pubgw-Signature": self._sign(self._secret, stamp, body)}
        request = Request(self._url + path, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        self.check("{} answer carries no engine password".format(path), self._password not in raw)
        return status, json.loads(raw.decode("utf-8"))

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        suffix = ": " + json.dumps(detail, ensure_ascii=False)[:400] if detail and not condition else ""
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label, suffix), file=sys.stderr)
        if not condition:
            self.failures.append(label)


def close_payload(instance: str, sid: int) -> Dict[str, Any]:
    return {"requestId": str(uuid.uuid4()), "engineInstanceId": instance, "surveyId": sid}


def drift_payload(binding: Dict[str, Any], with_binding: bool = True) -> Dict[str, Any]:
    payload = {"engineInstanceId": binding["engineInstance"], "surveyId": binding["surveyId"],
               "expectedFingerprint": binding["fingerprint"]}
    if with_binding:
        payload["binding"] = binding
    return payload


def cmd_republish(driver: Driver, args: argparse.Namespace) -> Dict[str, Any]:
    with open(args.definition, encoding="utf-8") as handle:
        definition = json.load(handle)
    old_sid = args.old_sid

    status, body = driver.post("/v1/publish", {"requestId": str(uuid.uuid4()), "engineInstanceId": args.instance,
                                               "definition": definition})
    binding = (body.get("result") or {}).get("binding") or {}
    new_sid = binding.get("surveyId")
    driver.check("republish (same definition uuid, new requestId) -> 200 published",
                 status == 200 and body.get("status") == "published", body)
    driver.check("the new version is a new engine survey", isinstance(new_sid, int) and new_sid != old_sid,
                 {"old": old_sid, "new": new_sid})

    status, body = driver.post("/v1/close", close_payload(args.instance, old_sid))
    result = body.get("result") or {}
    driver.check("close old sid -> 200 closed, not previously closed",
                 status == 200 and body.get("status") == "closed" and result.get("surveyId") == old_sid
                 and result.get("alreadyClosed") is False and bool(result.get("expires")), body)
    status, body = driver.post("/v1/close", close_payload(args.instance, old_sid))
    driver.check("closing again is harmless -> 200 alreadyClosed",
                 status == 200 and (body.get("result") or {}).get("alreadyClosed") is True, body)
    status, body = driver.post("/v1/close", close_payload(args.instance, MISSING_SID))
    driver.check("closing a sid the engine does not have -> 502 E_SURVEY_MISSING",
                 status == 502 and body.get("error") == "E_SURVEY_MISSING", body)

    status, body = driver.post("/v1/drift-check", drift_payload(binding))
    driver.check("drift-check of the untouched new version -> 200 match",
                 status == 200 and body.get("status") == "match"
                 and (body.get("result") or {}).get("currentFingerprint") == binding.get("fingerprint"), body)
    old_by_fingerprint = dict(drift_payload(binding, with_binding=False), surveyId=old_sid)
    status, body = driver.post("/v1/drift-check", old_by_fingerprint)
    driver.check("the closed old sid is not drift (expired is expected) -> 200 match",
                 status == 200 and body.get("status") == "match", body)

    Path(args.state).write_text(json.dumps({"binding": binding}), encoding="utf-8")
    return {"oldSid": old_sid, "newSid": new_sid}


def cmd_drift(driver: Driver, args: argparse.Namespace) -> Dict[str, Any]:
    binding = json.loads(Path(args.state).read_text(encoding="utf-8"))["binding"]
    old_code, new_code = DRIFTED_QUESTION

    status, body = driver.post("/v1/drift-check", drift_payload(binding))
    result = body.get("result") or {}
    renamed = result.get("renamed") or []
    driver.check("drift-check after the engine-side edit -> 200 drift",
                 status == 200 and body.get("status") == "drift" and result.get("drifted") is True, body)
    driver.check("the edited question is named: {} -> {}".format(old_code, new_code),
                 any(r.get("from") == old_code and r.get("to") == new_code for r in renamed), renamed)
    driver.check("E_CODE_DRIFT reported and the fingerprint moved",
                 "E_CODE_DRIFT" in [i.get("code") for i in result.get("issues") or []]
                 and result.get("currentFingerprint") != binding.get("fingerprint"), result)

    status, body = driver.post("/v1/drift-check", drift_payload(binding, with_binding=False))
    codes = [i.get("code") for i in (body.get("result") or {}).get("issues") or []]
    driver.check("fingerprint-only drift-check -> drift E_FINGERPRINT_DRIFT",
                 status == 200 and body.get("status") == "drift" and codes == ["E_FINGERPRINT_DRIFT"], body)
    return {"surveyId": binding.get("surveyId"), "currentFingerprint": result.get("currentFingerprint")}


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--gateway-dir", required=True)
    parser.add_argument("--state", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    republish = commands.add_parser("republish")
    republish.add_argument("--instance", required=True)
    republish.add_argument("--definition", required=True)
    republish.add_argument("--old-sid", type=int, required=True)
    commands.add_parser("drift")
    args = parser.parse_args(argv)

    sys.path.insert(0, args.gateway_dir)
    from pubgw.auth import sign  # noqa: E402 — 网关目录在运行时才加入路径

    driver = Driver(args.url, os.environ["PUBGW_SHARED_SECRET"].encode("utf-8"), os.environ["ENGINE_PASSWORD"], sign)
    summary = cmd_republish(driver, args) if args.command == "republish" else cmd_drift(driver, args)
    print(json.dumps(dict(summary, failures=driver.failures)))
    return 1 if driver.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
