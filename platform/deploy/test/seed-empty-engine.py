#!/usr/bin/env python3
"""Seed one disposable survey so LimeSurvey list_surveys has an empty-list baseline."""

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def rpc(url: str, method: str, params: list[object]) -> object:
    body = json.dumps({"method": method, "params": params, "id": 1}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error") is not None:
        raise RuntimeError("LimeSurvey RemoteControl request failed")
    return payload.get("result")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    password = os.environ.get("PUBGW_ENGINE_ADMIN_WEB_PASSWORD", "")
    if not password:
        raise SystemExit("PUBGW_ENGINE_ADMIN_WEB_PASSWORD is required")
    rpc_url = args.base_url.rstrip("/") + "/index.php/admin/remotecontrol"
    session = rpc(rpc_url, "get_session_key", ["admin", password])
    if not isinstance(session, str) or not session:
        raise RuntimeError("LimeSurvey RemoteControl login failed")
    try:
        encoded = base64.b64encode(args.fixture.read_bytes()).decode("ascii")
        survey_id = rpc(rpc_url, "import_survey", [session, encoded, "lss", "E2E runtime baseline"])
        if not isinstance(survey_id, int) or survey_id < 1:
            raise RuntimeError("LimeSurvey baseline import failed")
    finally:
        rpc(rpc_url, "release_session_key", [session])


if __name__ == "__main__":
    main()
