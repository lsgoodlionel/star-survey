"""Sanitized GitHub Gate summaries and strict local evidence import."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from .diagnostics import redact_text
from .gate_summary import GateSummary


class GitHubEvidenceError(ValueError):
    """GitHub evidence is malformed, untrusted, or bound to another run."""


@dataclass(frozen=True)
class VerifiedGitHubArtifact:
    artifact_sha256: str
    repository: str
    signer_workflow: str


def verify_github_attestation(path: Path, *, repository: str,
                              signer_workflow: str,
                              runner=subprocess.run) -> VerifiedGitHubArtifact:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise GitHubEvidenceError("GitHub evidence artifact is unavailable")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    signer_identity = repository + "/" + signer_workflow
    command = ["gh", "attestation", "verify", str(artifact),
               "--repo", repository, "--signer-workflow", signer_identity]
    try:
        result = runner(command, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubEvidenceError("GitHub artifact attestation is unavailable") from error
    if result.returncode != 0:
        raise GitHubEvidenceError("GitHub artifact attestation is invalid")
    return VerifiedGitHubArtifact(digest, repository, signer_workflow)


_FIELDS = {"version", "repository", "workflow", "commitSha", "job",
           "conclusion", "artifactPath", "summary", "summarySha256"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}")


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GitHubEvidenceError("GitHub evidence contains duplicate fields")
        result[key] = value
    return result


def _summary(document) -> GateSummary:
    if not isinstance(document, dict):
        raise GitHubEvidenceError("GitHub Gate summary is not an object")
    try:
        summary = GateSummary(
            document["schemaVersion"], document["gateId"], document["status"],
            document["exitCode"], document["durationMs"], document["counts"],
            document["evidenceKey"], document["headCommit"],
            document["diagnostic"], document["source"], document["reused"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubEvidenceError("GitHub Gate summary schema is invalid") from error
    if set(document) != {"schemaVersion", "gateId", "status", "exitCode",
                         "durationMs", "counts", "evidenceKey", "headCommit",
                         "source", "reused", "diagnostic"}:
        raise GitHubEvidenceError("GitHub Gate summary has unknown fields")
    return summary


def import_github_evidence(path: Path, *, expected_artifact_sha256: str,
                           repository: str, workflow: str, commit_sha: str,
                           job: str, gate_id: str,
                           attestation: VerifiedGitHubArtifact | None = None) -> GateSummary:
    artifact = Path(path)
    if artifact.is_symlink() or not artifact.is_file():
        raise GitHubEvidenceError("GitHub evidence artifact is unavailable")
    try:
        raw = artifact.read_bytes()
    except OSError as error:
        raise GitHubEvidenceError("GitHub evidence artifact is unavailable") from error
    if len(raw) > 16384 or _SHA256.fullmatch(expected_artifact_sha256) is None:
        raise GitHubEvidenceError("GitHub evidence artifact size or digest is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_artifact_sha256:
        raise GitHubEvidenceError("GitHub evidence artifact digest mismatch")
    if (not isinstance(attestation, VerifiedGitHubArtifact)
            or attestation.artifact_sha256 != expected_artifact_sha256
            or attestation.repository != repository
            or attestation.signer_workflow != workflow):
        raise GitHubEvidenceError("GitHub artifact attestation does not match")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GitHubEvidenceError("GitHub evidence artifact is malformed") from error
    if not isinstance(document, dict) or set(document) != _FIELDS or document["version"] != 1:
        raise GitHubEvidenceError("GitHub evidence envelope schema is invalid")
    expected = {
        "repository": repository, "workflow": workflow,
        "commitSha": commit_sha, "job": job,
    }
    if any(document[key] != value for key, value in expected.items()):
        raise GitHubEvidenceError("GitHub evidence identity does not match")
    if (_IDENTIFIER.fullmatch(repository) is None or ".." in repository
            or not workflow.startswith(".github/workflows/") or ".." in workflow
            or not workflow.endswith((".yml", ".yaml"))
            or _SHA1.fullmatch(commit_sha) is None
            or _IDENTIFIER.fullmatch(job) is None):
        raise GitHubEvidenceError("Expected GitHub evidence identity is invalid")
    artifact_path = document["artifactPath"]
    expected_path = f"gate-summaries/{gate_id}.json"
    if (not isinstance(artifact_path, str)
            or PurePosixPath(artifact_path).as_posix() != artifact_path
            or artifact_path != expected_path):
        raise GitHubEvidenceError("GitHub evidence artifact path is invalid")
    summary_document = document["summary"]
    if (_SHA256.fullmatch(str(document["summarySha256"])) is None
            or hashlib.sha256(_canonical(summary_document)).hexdigest()
            != document["summarySha256"]):
        raise GitHubEvidenceError("GitHub Gate summary digest mismatch")
    summary = _summary(summary_document)
    if (summary.gate_id != gate_id or summary.head_commit != commit_sha
            or summary.source != "github" or summary.reused):
        raise GitHubEvidenceError("GitHub Gate summary binding does not match")
    conclusion = document["conclusion"]
    if conclusion == "success":
        valid = (summary.status == "passed" and summary.exit_code == 0
                 and summary.diagnostic is None
                 and summary.counts["total"] > 0
                 and summary.counts["passed"] == summary.counts["total"]
                 and summary.counts["failed"] == 0
                 and summary.counts["skipped"] == 0
                 and summary.counts["errors"] == 0)
    elif conclusion == "failure":
        valid = (summary.status in {"failed", "timed_out"}
                 and summary.exit_code not in (None, 0)
                 and isinstance(summary.diagnostic, str)
                 and len(summary.diagnostic.encode("utf-8")) <= 4096
                 and redact_text(summary.diagnostic) == summary.diagnostic)
    else:
        valid = False
    if not valid:
        raise GitHubEvidenceError("GitHub conclusion and Gate summary disagree")
    counts = summary.counts
    if sum(counts[key] for key in ("passed", "failed", "skipped", "errors")) != counts["total"]:
        raise GitHubEvidenceError("GitHub Gate counts are inconsistent")
    return summary


def write_github_evidence(path: Path, *, repository: str, workflow: str,
                          commit_sha: str, job: str, gate_id: str,
                          conclusion: str) -> None:
    status = "passed" if conclusion == "success" else "failed"
    summary = {
        "schemaVersion": 1, "gateId": gate_id, "status": status,
        "exitCode": 0 if status == "passed" else 1, "durationMs": 0,
        "counts": {"total": 1, "passed": 1 if status == "passed" else 0,
                   "failed": 0 if status == "passed" else 1,
                   "skipped": 0, "errors": 0},
        "evidenceKey": None, "headCommit": commit_sha, "source": "github",
        "reused": False,
        "diagnostic": None if status == "passed" else "GitHub job failed; inspect the retained workflow log.",
    }
    envelope = {
        "version": 1, "repository": repository, "workflow": workflow,
        "commitSha": commit_sha, "job": job, "conclusion": conclusion,
        "artifactPath": f"gate-summaries/{gate_id}.json", "summary": summary,
        "summarySha256": hashlib.sha256(_canonical(summary)).hexdigest(),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False,
                                         prefix="." + target.name + ".") as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(envelope) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--conclusion", choices=("success", "failure"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    write_github_evidence(args.output, repository=args.repository,
                          workflow=args.workflow, commit_sha=args.commit_sha,
                          job=args.job, gate_id=args.gate_id,
                          conclusion=args.conclusion)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(f"### Gate `{args.gate_id}`\n\n- 结论：`{args.conclusion}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
