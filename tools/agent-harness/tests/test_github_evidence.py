"""GitHub Gate evidence crosses a strict, digest-bound trust boundary."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.github_evidence import (  # noqa: E402
    GitHubEvidenceError,
    import_github_evidence,
    verify_github_attestation,
)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_service import WorktreeCase  # noqa: E402
from agent_harness.run_service import ServiceError  # noqa: E402


class GitHubEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "summary.json"
        self.summary = {
            "schemaVersion": 1, "gateId": "harness-unit", "status": "passed",
            "exitCode": 0, "durationMs": 123,
            "counts": {"total": 8, "passed": 8, "failed": 0,
                       "skipped": 0, "errors": 0},
            "evidenceKey": "b" * 64, "headCommit": "a" * 40,
            "source": "github", "reused": False, "diagnostic": None,
        }
        self.document = {
            "version": 1, "repository": "owner/repo",
            "workflow": ".github/workflows/agent-governance.yml",
            "commitSha": "a" * 40, "job": "agent-governance-unit",
            "conclusion": "success",
            "artifactPath": "gate-summaries/harness-unit.json",
            "summary": self.summary,
            "summarySha256": self.digest(self.summary),
        }

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=True).encode()).hexdigest()

    def write(self, document=None):
        self.path.write_text(json.dumps(document or self.document, sort_keys=True,
                                        separators=(",", ":")), encoding="utf-8")
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load(self, document=None, digest=None):
        actual = self.write(document)
        attestation = verify_github_attestation(
            self.path, repository="owner/repo",
            signer_workflow=".github/workflows/agent-governance.yml",
            runner=lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
        )
        return import_github_evidence(
            self.path, expected_artifact_sha256=digest or actual,
            repository="owner/repo",
            workflow=".github/workflows/agent-governance.yml",
            commit_sha="a" * 40, job="agent-governance-unit",
            gate_id="harness-unit", attestation=attestation,
        )

    def test_accepts_exact_success_summary_without_raw_logs(self):
        summary = self.load()
        self.assertEqual(summary.gate_id, "harness-unit")
        self.assertEqual(summary.source, "github")
        self.assertIsNone(summary.diagnostic)

    def test_rejects_each_identity_schema_and_digest_mismatch(self):
        mutations = {
            "repository": lambda d: d.update(repository="other/repo"),
            "workflow": lambda d: d.update(workflow=".github/workflows/other.yml"),
            "commit": lambda d: d.update(commitSha="c" * 40),
            "job": lambda d: d.update(job="other-job"),
            "conclusion": lambda d: d.update(conclusion="failure"),
            "schema": lambda d: d.update(version=2),
            "artifact_path": lambda d: d.update(artifactPath="../raw.log"),
            "summary_digest": lambda d: d.update(summarySha256="0" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                document = json.loads(json.dumps(self.document))
                mutate(document)
                with self.assertRaises(GitHubEvidenceError):
                    self.load(document)
        with self.assertRaises(GitHubEvidenceError):
            self.load(digest="f" * 64)

    def test_failure_diagnostic_is_bounded_sanitized_and_raw_fields_are_rejected(self):
        document = json.loads(json.dumps(self.document))
        document["conclusion"] = "failure"
        document["summary"].update(status="failed", exitCode=1,
                                   diagnostic="TOKEN=fake-secret")
        document["summarySha256"] = self.digest(document["summary"])
        with self.assertRaises(GitHubEvidenceError):
            self.load(document)

    def test_rejects_missing_attestation_and_success_with_skips(self):
        digest = self.write()
        with self.assertRaises(GitHubEvidenceError):
            import_github_evidence(
                self.path, expected_artifact_sha256=digest,
                repository="owner/repo",
                workflow=".github/workflows/agent-governance.yml",
                commit_sha="a" * 40, job="agent-governance-unit",
                gate_id="harness-unit",
            )
        document = json.loads(json.dumps(self.document))
        document["summary"]["counts"] = {
            "total": 8, "passed": 7, "failed": 0, "skipped": 1, "errors": 0,
        }
        document["summarySha256"] = self.digest(document["summary"])
        with self.assertRaises(GitHubEvidenceError):
            self.load(document)
        document = json.loads(json.dumps(self.document))
        document["rawLog"] = "private output"
        with self.assertRaises(GitHubEvidenceError):
            self.load(document)


class GitHubRunServiceTests(WorktreeCase):
    def test_imports_digest_bound_success_as_current_head_gate_evidence(self):
        host = self.repo / "docs/agent/HARNESS_HOST.json"
        self.write(host, json.dumps({
            "expectedRepository": "owner/repo",
            "repositoryAttestation": {
                "provider": "github-artifact-attestation",
                "signerWorkflow": ".github/workflows/agent-governance.yml",
                "sourceRef": "refs/heads/main",
            },
        }))
        self.git("add", "docs/agent/HARNESS_HOST.json")
        self.git("commit", "-m", "github identity")
        self.init_run()
        commit = self.git("rev-parse", "HEAD")
        summary = {
            "schemaVersion": 1, "gateId": "unit", "status": "passed",
            "exitCode": 0, "durationMs": 1,
            "counts": {"total": 1, "passed": 1, "failed": 0,
                       "skipped": 0, "errors": 0},
            "evidenceKey": None, "headCommit": commit, "source": "github",
            "reused": False, "diagnostic": None,
        }
        document = {
            "version": 1, "repository": "owner/repo",
            "workflow": ".github/workflows/agent-governance.yml",
            "commitSha": commit, "job": "agent-governance-unit",
            "conclusion": "success", "artifactPath": "gate-summaries/unit.json",
            "summary": summary,
            "summarySha256": GitHubEvidenceTests.digest(summary),
        }
        artifact = self.root / "downloaded-summary.json"
        artifact.write_text(json.dumps(document, sort_keys=True,
                                       separators=(",", ":")), encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with patch("agent_harness.run_service.verify_github_attestation") as verify:
            from agent_harness.github_evidence import VerifiedGitHubArtifact
            verify.return_value = VerifiedGitHubArtifact(
                digest, "owner/repo", ".github/workflows/agent-governance.yml")
            state = self.service.import_github_gate(
                self.state.run_id, artifact, artifact_sha256=digest,
                workflow=".github/workflows/agent-governance.yml",
                job="agent-governance-unit", gate_id="unit")
        self.assertEqual(state.gates["unit"].source, "github")
        matrix, _ = self.service._configs()
        self.service._verify_evidence(state, matrix.gates[0])
        summary_path = self.repo / state.gates["unit"].summary_path
        summary_path.write_text(summary_path.read_text().replace('"durationMs":1',
                                                                 '"durationMs":2'))
        with self.assertRaises(ServiceError):
            self.service._verify_evidence(state, matrix.gates[0])


if __name__ == "__main__":
    unittest.main()
