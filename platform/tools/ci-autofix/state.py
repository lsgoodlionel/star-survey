#!/usr/bin/env python3
"""GitHub Actions controls retry state; Codex only edits source."""
import argparse
import json
import re
import subprocess
import sys

MARKER = re.compile(r"\[codex-fix-attempt: ([0-9]+)\]")
MAX_ATTEMPTS = 2


def attempt(message):
    match = MARKER.search(message)
    return int(match.group(1)) if match else 0


def plan(event, message):
    run = event.get("workflow_run", {})
    repo = event.get("repository", {})
    head_repo = run.get("head_repository") or {}
    prs = run.get("pull_requests") or []
    if (run.get("name") != "MJY 平台 - 质量闸门"
            or run.get("conclusion") != "failure" or run.get("event") != "pull_request"
            or head_repo.get("id") != repo.get("id") or not prs
            or not run.get("head_branch") or not run.get("head_sha")):
        return {"decision": "skip", "attempt": 0}
    return {
        "decision": "report" if attempt(message) >= MAX_ATTEMPTS else "fix",
        "attempt": attempt(message),
        "next_attempt": attempt(message) + 1,
        "branch": run["head_branch"],
        "sha": run["head_sha"],
        "pr": prs[0]["number"],
        "run_id": run["id"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("event")
    args = parser.parse_args()
    with open(args.event, encoding="utf-8") as stream:
        event = json.load(stream)
    sha = event.get("workflow_run", {}).get("head_sha", "")
    message = subprocess.check_output(["git", "show", "-s", "--format=%B", sha], text=True) if sha else ""
    result = plan(event, message)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"无法确定修复状态：{exc}", file=sys.stderr)
        sys.exit(1)
