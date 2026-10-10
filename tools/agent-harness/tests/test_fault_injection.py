"""End-to-end fault drills against the real Harness boundaries."""

import importlib.util
import hashlib
import io
import json
import os
import shutil
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "tools/agent-harness/drills/run_drills.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("agent_harness_drills", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FaultInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def assert_drill_passes(self, name):
        result = self.runner.run_named_drill(name)
        self.assertEqual(result.status, "passed", result.detail)
        self.assertNotIn("fake-secret", result.detail)

    def validate_review_document(self, root, content, *, manifest_verdict="APPROVED"):
        root = root.resolve()
        base = "a" * 40
        commit = "b" * 40
        reviewed_range = base + ".." + commit
        reports = []
        for reviewer in ("security", "dx_ci", "whole_branch"):
            report_path = root / f"reviews/{reviewer}.md"
            report_path.parent.mkdir(exist_ok=True)
            report_path.write_text(
                "Reviewer: " + reviewer + "\nReviewed range: `" + reviewed_range + "`\n\n"
                + content,
                encoding="utf-8",
            )
            reports.append({
                "reviewer": reviewer,
                "path": report_path.relative_to(root).as_posix(),
                "reviewedCommit": commit,
                "reviewedRange": reviewed_range,
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                "specVerdict": manifest_verdict,
                "codeQualityVerdict": manifest_verdict,
            })
        manifest = {
            "implementationCommit": commit,
            "reviewEvidence": {
                "requiredReviewers": ["security", "dx_ci", "whole_branch"],
                "reports": reports,
            },
        }
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(self.runner, "_run", return_value=completed):
            self.runner._validate_review_evidence(root, manifest, True)

    def review_manifest(self, root):
        root = root.resolve()
        base = "a" * 40
        commit = "b" * 40
        reviewed_range = base + ".." + commit
        reports = []
        for reviewer in ("security", "dx_ci", "whole_branch"):
            report_path = root / f"reviews/{reviewer}.md"
            report_path.parent.mkdir(exist_ok=True)
            report_path.write_text(
                "# " + reviewer + "\n\nReviewed range: `" + reviewed_range + "`\n\n"
                "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n",
                encoding="utf-8",
            )
            reports.append({
                "reviewer": reviewer,
                "path": report_path.relative_to(root).as_posix(),
                "reviewedCommit": commit,
                "reviewedRange": reviewed_range,
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                "specVerdict": "APPROVED",
                "codeQualityVerdict": "APPROVED",
            })
        return {
            "implementationCommit": commit,
            "reviewEvidence": {
                "requiredReviewers": ["security", "dx_ci", "whole_branch"],
                "reports": reports,
            },
        }

    def test_process_termination_before_atomic_replace_keeps_old_state(self):
        self.assert_drill_passes("atomic-before-replace")

    def test_process_termination_after_atomic_replace_keeps_new_state(self):
        self.assert_drill_passes("atomic-after-replace")

    def test_truncated_event_is_rejected(self):
        self.assert_drill_passes("truncated-events")

    def test_changed_plan_digest_pauses_resume(self):
        result = self.runner.run_named_drill("plan-digest-drift")
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(result.detail, "paused: plan digest drift")

    def test_rewritten_git_history_pauses_resume(self):
        self.assert_drill_passes("rewritten-history")

    def test_missing_docker_node_and_java_are_typed(self):
        self.assert_drill_passes("missing-runtimes")

    def test_fake_secret_is_redacted_from_diagnostics(self):
        self.assert_drill_passes("fake-secret-diagnostics")

    def test_repeated_gate_failure_stops_at_existing_threshold(self):
        self.assert_drill_passes("repeated-gate-failure")

    def test_codex_timeout_is_bounded_and_typed(self):
        self.assert_drill_passes("codex-timeout")

    def test_default_cli_runs_only_fake_drills(self):
        with patch.object(self.runner, "run_real_codex_smoke") as real, redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.main([]), 0)
        real.assert_not_called()

    def test_real_codex_flag_invokes_one_smoke_only(self):
        result = self.runner.DrillResult("real-codex-smoke", "paused", "sanitized pause")
        custom = Path("config/custom-host.json")
        with patch.object(self.runner, "ensure_no_production_secret_paths") as guard, \
                patch.object(self.runner, "run_real_codex_smoke", return_value=result) as real, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.main([
                "--allow-real-codex", "--host-config", custom.as_posix()
            ]), 0)
        real.assert_called_once_with(REPO, custom)
        guard.assert_not_called()

    def test_real_codex_refuses_production_secret_paths_even_if_tracked(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                           cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"],
                           cwd=repo, check=True, capture_output=True)
            secret = repo / ".aws/credentials"
            secret.parent.mkdir()
            secret.write_text("token=fake-secret\n", encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            subprocess.run(["git", "add", ".aws/credentials"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "bad secret fixture"], cwd=repo,
                           check=True, capture_output=True)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())

    @staticmethod
    def secret_policy():
        return {
            "patterns": [".env", ".env.*", "**/.env", "**/.env.*", ".aws", ".aws/**",
                         "**/.aws", "**/.aws/**", "**/secrets/**", "**/*.key"],
            "allowlist": [".env.example"],
        }

    def test_secret_inventory_covers_ignored_directories_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            (repo / ".gitignore").write_text("/var/\n", encoding="utf-8")
            secret = repo / "var/secrets/prod.key"
            secret.parent.mkdir(parents=True)
            secret.write_text("fixture\n", encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            secret.unlink()
            secret.parent.rmdir()
            (repo / "var").rmdir()
            (repo / ".aws").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())

    def test_secret_templates_are_allowed_only_by_host_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            (repo / ".env.example").write_text("TOKEN=replace-me\n", encoding="utf-8")
            self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            policy = {**self.secret_policy(), "allowlist": []}
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, policy)

    def test_forbidden_scan_reads_multiline_workflow_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/bad.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  bad:\n    steps:\n      - run: |\n"
                "          codex exec --dangerously-bypass-approvals-and-sandbox task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)
            workflow.write_text(
                "jobs:\n  x:\n    steps:\n      - ? \"\\\\u0072un\"\n"
                "        : codex exec --sandbox danger-full-access task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_covers_yaml_and_codex_equivalent_overrides_only(self):
        dangerous = (
            "codex exec --sandbox danger-full-access task",
            "codex exec --add-dir /tmp task",
            "codex exec --config approval_policy=never task",
            "codex exec -c sandbox_mode=danger-full-access task",
            "codex exec --dangerously-bypass-hook-trust task",
        )
        for index, command in enumerate(dangerous):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/bad-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  bad:\n    steps:\n      - run: " + command + "\n",
                                    encoding="utf-8")
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/benign.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("jobs:\n  ok:\n    steps:\n      - run: docker --config /tmp build .\n",
                                encoding="utf-8")
            try:
                self.runner.check_forbidden_options(repo)
            except self.runner.DrillRefused as error:
                self.fail(f"ordinary workflow content was refused: {error}")

    def test_forbidden_scan_rejects_shell_token_rewriting_in_codex_run(self):
        sources = (
            'codex exec --sandbox danger-full-"access" task',
            'MODE=access\ncodex exec --sandbox danger-full-$MODE task',
            'codex exec --sandbox danger-full-\\\n          access task',
            'codex exec --sandbox "$(printf danger-full-access)" task',
            'printf safe; codex exec --sandbox danger-full-access task',
            '$(printf codex) exec --sandbox danger-full-access task',
            'codex exec --sandbox workspace-write task; '
            'codex exec --sandbox danger-full-access task',
        )
        for index, source in enumerate(sources):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/rewrite-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  bad:\n    steps:\n      - run: |\n          " +
                                    source.replace("\n", "\n          ") + "\n", encoding="utf-8")
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/unrelated.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text('jobs:\n  ok:\n    steps:\n      - run: echo "codex"\n',
                                encoding="utf-8")
            self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_finds_quoted_run_keys_and_split_codex_executables(self):
        workflows = (
            '- "run" : codex exec --sandbox danger-full-access task',
            "- 'run': co\"\"dex exec --sandbox danger-full-access task",
            "- run : co${EMPTY}dex exec --sandbox danger-full-access task",
        )
        for index, step in enumerate(workflows):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/discovery-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  bad:\n    steps:\n      " + step + "\n",
                                    encoding="utf-8")
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_rejects_codex_executable_wrappers(self):
        commands = (
            "command codex exec --sandbox danger-full-access task",
            "command -- codex exec --sandbox danger-full-access task",
            "env codex exec --sandbox danger-full-access task",
            "env NAME=value codex exec --sandbox danger-full-access task",
            "/usr/bin/env codex exec --sandbox danger-full-access task",
            "/usr/bin/command codex exec --sandbox danger-full-access task",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/wrapper-{index}.yml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text(
                    "jobs:\n  bad:\n    steps:\n      - run: " + command + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_decodes_supported_quoted_inline_scalars(self):
        steps = (
            ("- run: 'printf safe'", False),
            ('- run: "printf safe"', False),
            ("- run: 'codex exec --sandbox danger-full-access task'", True),
            ('- run: "codex\\u0020exec --sandbox danger-full-access task"', True),
        )
        for index, (step, refused) in enumerate(steps):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/quoted-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  x:\n    steps:\n      " + step + "\n",
                                    encoding="utf-8")
                if refused:
                    with self.assertRaises(self.runner.DrillRefused):
                        self.runner.check_forbidden_options(repo)
                else:
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_rejects_codex_execution_sinks_and_expansions(self):
        commands = (
            "/bin/sh -c 'codex exec --sandbox danger-full-access task'",
            'bash -c "codex exec --sandbox danger-full-access task"',
            "bash -lc 'codex exec --sandbox danger-full-access task'",
            "zsh -c 'codex exec --sandbox danger-full-access task'",
            "eval 'codex exec --sandbox danger-full-access task'",
            "eval -- 'codex exec --sandbox danger-full-access task'",
            "./co[d]ex exec --sandbox danger-full-access task",
            "./co{d,d}ex exec --sandbox danger-full-access task",
            "/usr/local/bin/codex exec --sandbox danger-full-access task",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/sink-{index}.yml"
                workflow.parent.mkdir(parents=True)
                scalar = "|\n          " + command if index == 2 else command
                workflow.write_text("jobs:\n  x:\n    steps:\n      - run: " + scalar + "\n",
                                    encoding="utf-8")
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_allows_metadata_data_arguments_and_safe_nested_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/ordinary.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: codex exec documentation\n"
                "jobs:\n  x:\n    steps:\n"
                "      - run: grep codex exec docs/reference.txt\n"
                "      - run: python tool.py codex exec docs\n"
                "      - run: /bin/sh -c 'printf safe'\n"
                "      - run: |\n"
                "          jq -n --arg image \"$IMAGE\" \\\n"
                "            '{image: $image}' > out.json\n",
                encoding="utf-8",
            )
            try:
                self.runner.check_forbidden_options(repo)
            except self.runner.DrillRefused as error:
                self.fail(f"ordinary workflow content was refused: {error}")

    def test_forbidden_scan_refuses_folded_yaml_and_explicit_run_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/folded.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  x:\n    steps:\n      - run: >\n"
                "          echo safe\n\n"
                "          codex exec --sandbox danger-full-access task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)
            workflow.write_text(
                "jobs:\n  x:\n    steps:\n      - ? run\n"
                "        : codex exec --sandbox danger-full-access task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)
            workflow.write_text(
                "jobs:\n  x:\n    steps:\n      - ?\n"
                "          run\n"
                "        : codex exec --sandbox danger-full-access task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_refuses_recursive_wrappers_and_quote_spelling(self):
        commands = (
            'c\'o\'dex exec --sandbox danger-full-access task',
            'co"d"ex exec --sandbox danger-full-access task',
            "exec codex exec --sandbox danger-full-access task",
            "timeout 1 codex exec --sandbox danger-full-access task",
            "env bash -c 'codex exec --sandbox danger-full-access task'",
            "command bash -c 'codex exec --sandbox danger-full-access task'",
            "printf x | xargs codex exec --sandbox danger-full-access task",
            "if true; then codex exec --sandbox danger-full-access task; fi",
            "./c?dex exec --sandbox danger-full-access task",
            "./cod?x exec --sandbox danger-full-access task",
            "./code? exec --sandbox danger-full-access task",
            "./*odex exec --sandbox danger-full-access task",
            "./c[o]dex exec --sandbox danger-full-access task",
            "./c{o,o}dex exec --sandbox danger-full-access task",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/wrapper-final-{index}.yml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text(
                    "jobs:\n  x:\n    steps:\n      - run: |\n          "
                    + command + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_reports_relative_workflow_path_and_line_without_command(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/second.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  x:\n    steps:\n      - run: "
                "codex exec --sandbox danger-full-access fake-secret-value\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused) as raised:
                self.runner.check_forbidden_options(repo)
            message = str(raised.exception)
            self.assertIn(".github/workflows/second.yml:4:", message)
            self.assertNotIn("fake-secret-value", message)
            self.assertNotIn("codex exec", message)

    def test_forbidden_scan_rejects_unsupported_yaml_with_codex_commands(self):
        workflows = (
            '- { run: codex exec --sandbox danger-full-access task }',
            '- { "run": codex exec --sandbox danger-full-access task }',
            '- { "\\u0072un": codex exec --sandbox danger-full-access task }',
            '- "\\u0072un": codex exec --sandbox danger-full-access task',
            '- !!str run: codex exec --sandbox danger-full-access task',
            '- run: !!str codex exec --sandbox danger-full-access task',
            '- run: &command codex exec --sandbox danger-full-access task',
            '- run: *command',
            '- run: |+\n          codex exec --sandbox danger-full-access task',
            "- run: 'unterminated",
            '- run: "unterminated',
        )
        for index, step in enumerate(workflows):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/yaml-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                prefix = "x-command: &command codex exec --sandbox danger-full-access task\n" \
                    if "*command" in step else ""
                workflow.write_text(
                    prefix + "jobs:\n  bad:\n    steps:\n      " + step + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)

    def test_forbidden_scan_allows_only_normalized_codex_argv(self):
        commands = (
            ("codex exec --sandbox workspace-write --json task", False),
            ("codex exec --sandbox workspace-write --mystery value task", True),
            ("codex exec --json first second", True),
        )
        for index, (command, refused) in enumerate(commands):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/argv-{index}.yml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  argv:\n    steps:\n      - run: " + command + "\n",
                                    encoding="utf-8")
                if refused:
                    with self.assertRaises(self.runner.DrillRefused):
                        self.runner.check_forbidden_options(repo)
                else:
                    self.runner.check_forbidden_options(repo)

    def test_review_verdict_requires_one_canonical_approved_block_at_eof(self):
        approved = "# Review\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
        with tempfile.TemporaryDirectory() as directory:
            self.validate_review_document(Path(directory), approved)
        rejected = {
            "historical_then_final": (
                "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n\n"
                "SPEC_COMPLIANCE=CHANGES_REQUIRED\nCODE_QUALITY=CHANGES_REQUIRED\n"
            ),
            "quoted": (
                "> SPEC_COMPLIANCE=APPROVED\n> CODE_QUALITY=APPROVED\n"
            ),
            "code_block": (
                "```text\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n```\n"
            ),
            "duplicate": (
                "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n\n"
                "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "missing": "SPEC_COMPLIANCE=APPROVED\n",
            "reversed": "CODE_QUALITY=APPROVED\nSPEC_COMPLIANCE=APPROVED\n",
            "trailing_text": (
                "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\nLater note\n"
            ),
            "unclosed_fence": (
                "```text\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "unclosed_html_comment": (
                "<!--\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "closed_html_comment": (
                "<!--\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n-->\n"
            ),
            "indented_code": (
                "    SPEC_COMPLIANCE=APPROVED\n    CODE_QUALITY=APPROVED\n"
            ),
            "lazy_blockquote": (
                "> quoted review\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "nested_list_container": (
                "- review result\n\n  SPEC_COMPLIANCE=APPROVED\n"
                "  CODE_QUALITY=APPROVED\n"
            ),
            "raw_script": (
                "<script>\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "raw_pre": (
                "<pre>\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "processing_instruction": (
                "<?review\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "cdata": (
                "<![CDATA[\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
            "html_declaration": (
                "<!REVIEW\n\nSPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n"
            ),
        }
        for name, content in rejected.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory, \
                    self.assertRaises(ValueError):
                self.validate_review_document(Path(directory), content)

    def test_review_verdicts_must_match_manifest_and_be_approved(self):
        content = "SPEC_COMPLIANCE=CHANGES_REQUIRED\nCODE_QUALITY=CHANGES_REQUIRED\n"
        for manifest_verdict in ("APPROVED", "CHANGES_REQUIRED"):
            with self.subTest(manifest=manifest_verdict), \
                    tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                self.validate_review_document(
                    Path(directory), content, manifest_verdict=manifest_verdict
                )

    def test_review_evidence_requires_three_unique_paths_and_digests(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest = self.review_manifest(root)
            with patch.object(self.runner, "_run", return_value=completed):
                self.runner._validate_review_evidence(root, manifest, True)

            reports = manifest["reviewEvidence"]["reports"]
            duplicate_cases = {
                "two_same": ((1, 0),),
                "all_same": ((1, 0), (2, 0)),
            }
            for name, replacements in duplicate_cases.items():
                with self.subTest(case=name):
                    duplicate = json.loads(json.dumps(manifest))
                    for target, source in replacements:
                        duplicate["reviewEvidence"]["reports"][target]["path"] = \
                            reports[source]["path"]
                        duplicate["reviewEvidence"]["reports"][target]["sha256"] = \
                            reports[source]["sha256"]
                    with patch.object(self.runner, "_run", return_value=completed), \
                            self.assertRaises(ValueError):
                        self.runner._validate_review_evidence(root, duplicate, True)

            same_digest = json.loads(json.dumps(manifest))
            second = root / same_digest["reviewEvidence"]["reports"][1]["path"]
            first = root / reports[0]["path"]
            second.write_bytes(first.read_bytes())
            same_digest["reviewEvidence"]["reports"][1]["sha256"] = hashlib.sha256(
                second.read_bytes()).hexdigest()
            with patch.object(self.runner, "_run", return_value=completed), \
                    self.assertRaises(ValueError):
                self.runner._validate_review_evidence(root, same_digest, True)

    def test_review_evidence_rejects_symlink_alias(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest = self.review_manifest(root)
            reports = manifest["reviewEvidence"]["reports"]
            alias = root / "reviews/dx-alias.md"
            alias.symlink_to(root / reports[1]["path"])
            reports[1]["path"] = alias.relative_to(root).as_posix()
            with patch.object(self.runner, "_run", return_value=completed), \
                    self.assertRaises(ValueError):
                self.runner._validate_review_evidence(root, manifest, True)

    def test_smoke_inputs_refuse_symlinks_without_touching_external_files(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory)
            external = Path(outside) / "sentinel.txt"
            external.write_text("keep\n", encoding="utf-8")
            plan = repo / "plan.md"
            plan.symlink_to(external)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.atomic_create(repo, plan, "replacement\n")
            self.assertEqual(external.read_text(encoding="utf-8"), "keep\n")
            plan.unlink()
            fixture = repo / "fixture.txt"
            fixture.symlink_to(external)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.atomic_create(repo, fixture, "replacement\n")
            self.assertEqual(external.read_text(encoding="utf-8"), "keep\n")

    def test_atomic_create_parent_swap_cannot_write_outside_fixed_root(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory).resolve()
            parent = repo / "docs/plans"
            parent.mkdir(parents=True)
            moved = repo / "docs/original-plans"
            external = Path(outside) / "plan.md"
            real_open = os.open
            swapped = False

            def swap_before_create(path, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped and flags & os.O_CREAT:
                    swapped = True
                    parent.rename(moved)
                    parent.symlink_to(outside, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(self.runner.os, "open", side_effect=swap_before_create), \
                    self.assertRaises(self.runner.DrillRefused):
                self.runner.atomic_create(repo, parent / "plan.md", "escaped\n")
            self.assertTrue(swapped)
            self.assertFalse(external.exists())

    def test_completed_smoke_requires_exact_fixture_and_nonempty_head_bound_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.txt"
            fixture.write_text("baseline\nwrong\n", encoding="utf-8")
            history = root / "history.md"
            history.write_text("sanitized\n", encoding="utf-8")
            state = self.runner._state(root, self.runner.RunStatus.COMPLETED)
            with self.assertRaises(AssertionError):
                self.runner.validate_real_smoke_terminal(
                    root, state, ("fixture.txt",), fixture, history,
                    "baseline\nreal-codex-smoke-ok\n",
                )

    def test_paused_smoke_rejects_unsanitized_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.txt"
            fixture.write_text("baseline\n", encoding="utf-8")
            history = root / "history.md"
            history.write_text("Authorization: Bearer fake-secret\n", encoding="utf-8")
            state = self.runner._state(root, self.runner.RunStatus.PAUSED)
            with self.assertRaises(AssertionError):
                self.runner.validate_real_smoke_terminal(root, state, (), fixture, history,
                                                        "baseline\nreal-codex-smoke-ok\n")

    def test_cleanup_failure_is_typed_and_never_claims_completion(self):
        failed = subprocess.CompletedProcess([], 1, "", "failed")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.runner, "_run", return_value=failed):
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            errors = self.runner.cleanup_real_smoke(root, worktree, "drill/test")
        self.assertTrue(errors)
        self.assertNotIn("removed", " ".join(errors).lower())

    def test_cleanup_oserror_at_each_git_step_accumulates_and_continues(self):
        stages = ("worktree remove", "worktree prune", "branch -D", "worktree list", "show-ref --verify")
        for failed_stage in stages:
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                worktree = root / "worktree"
                worktree.mkdir()
                calls = []

                def execute(args, cwd, check=False):
                    stage = " ".join(args[1:3])
                    calls.append(stage)
                    if stage == failed_stage:
                        raise OSError("fake-secret")
                    code = 1 if stage == "show-ref --verify" else 0
                    return subprocess.CompletedProcess(args, code, "", "")

                errors = self.runner.cleanup_real_smoke(root, worktree, "drill/test", execute=execute)
                self.assertEqual(len(calls), 5)
                self.assertTrue(any("oserror" in error for error in errors), errors)
                self.assertNotIn("fake-secret", repr(errors))

    def test_outer_cleanup_and_identity_errors_are_typed_without_short_circuit(self):
        identity = ("a" * 40, b"")
        cases = ((True, False), (False, True), (True, True))
        for cleanup_fails, identity_fails in cases:
            with self.subTest(cleanup=cleanup_fails, identity=identity_fails), \
                    tempfile.TemporaryDirectory() as directory:
                temporary = type("Temporary", (), {
                    "name": directory,
                    "cleanup": lambda self: (_ for _ in ()).throw(
                        OSError("PASSWORD=fake-secret")) if cleanup_fails else None,
                })()
                identities = [identity]
                if identity_fails:
                    identities.append(OSError("TOKEN=fake-secret"))
                else:
                    identities.append(identity)
                with patch.object(self.runner.tempfile, "TemporaryDirectory",
                                  return_value=temporary), \
                        patch.object(self.runner, "_primary_identity",
                                     side_effect=identities) as checked, \
                        patch.object(self.runner, "_git",
                                     side_effect=OSError("setup failed")), \
                        patch.object(self.runner, "cleanup_real_smoke", return_value=()):
                    result = self.runner.run_real_codex_smoke(REPO)
                self.assertEqual(checked.call_count, 2)
                self.assertEqual(result.status, "failed")
                if cleanup_fails:
                    self.assertIn("temporary-cleanup-oserror", result.detail)
                if identity_fails:
                    self.assertIn("primary-identity-check-oserror", result.detail)
                self.assertNotIn("fake-secret", result.detail)
                self.assertNotIn("verified", result.detail)

    def test_success_drill_runs_service_adapter_gate_review_finalize_and_history(self):
        result = self.runner.run_named_drill("success")
        self.assertEqual(result.status, "passed", result.detail)
        detail = json.loads(result.detail)
        self.assertEqual(detail["status"], "completed")
        expected = ["codex_started", "codex_finished", "gate_finished",
                    "review_recorded", "completed"]
        positions = [detail["events"].index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(detail["gateHead"], detail["headCommit"])
        self.assertEqual(detail["reviewHead"], detail["headCommit"])
        self.assertEqual(detail["reviewScope"], detail["expectedReviewScope"])
        self.assertTrue(detail["historyPublished"])

    def test_old_python_gets_concise_version_diagnostic_before_core_imports(self):
        probe = ("import runpy,sys; sys.version_info=(3,10,0); "
                 "runpy.run_path(sys.argv[1], run_name='__main__')")
        result = subprocess.run([sys.executable, "-I", "-c", probe, str(RUNNER), "--help"],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python 3.11", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_manifest_drives_plan_ledger_and_host_document_status(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo,
                           check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo,
                           check=True, capture_output=True)
            (repo / ".gitignore").write_text("/var/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True,
                           capture_output=True)
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  text=True, capture_output=True).stdout.strip()
            plan = repo / "docs/superpowers/plans/custom.md"
            plan.parent.mkdir(parents=True)
            plan.write_text("# Plan\n\n### Task 1: Base\n- [x] done\n\n"
                            "### Task 8: Delivery\n- [ ] sync\n", encoding="utf-8")
            marker = "<!-- harness-delivery-status: local_validated_sync_pending -->"
            for relative in ("README.md", "AGENTS.md", "docs/AUTONOMY.md", "docs/MEMORY.md"):
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(marker + "\n", encoding="utf-8")
            ledger = repo / ".sdd/progress.md"
            ledger.parent.mkdir()
            ledger.write_text("Task 1: complete\nTask 8: in_progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "host fixture"], cwd=repo, check=True,
                           capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  text=True, capture_output=True).stdout.strip()
            manifest = repo / "docs/DELIVERY.json"
            manifest.write_text(json.dumps({
                "version": 1,
                "planPath": "docs/superpowers/plans/custom.md",
                "implementationCommit": head,
                "evidence": {"headCommit": head, "status": "passed", "commands": ["unit"]},
                "taskSteps": {"1": {"completed": [1], "pending": []},
                              "8": {"completed": [], "pending": [1]}},
                "deliveryStatus": "local_validated_sync_pending",
                "externalSync": {"independentReview": "pending", "github": "pending",
                                 "obsidian": "pending"},
                "reviewEvidence": {
                    "requiredReviewers": ["security", "dx_ci", "whole_branch"],
                    "reports": [],
                },
            }, sort_keys=True), encoding="utf-8")
            config = repo / "docs/HOST.json"
            config.write_text(json.dumps({
                "version": 1,
                "activePlan": "docs/superpowers/plans/custom.md",
                "deliveryManifest": "docs/DELIVERY.json",
                "ledger": ".sdd/progress.md",
                "secretPolicy": self.secret_policy(),
                "fixture": {"path": "fixture.txt", "planPath": "docs/superpowers/plans/smoke.md",
                            "baseline": "baseline\\n", "expected": "baseline\\nok\\n"},
                "documentation": {"statusMarkerPaths": ["README.md", "AGENTS.md",
                                                          "docs/AUTONOMY.md", "docs/MEMORY.md"]},
            }, sort_keys=True), encoding="utf-8")
            host = self.runner.load_host_config(repo, config.relative_to(repo))
            self.runner.check_docs(repo, host)
            manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_document["externalSync"]["independentReview"] = "complete"
            manifest_document["deliveryStatus"] = "locally_reviewed_sync_pending"
            manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_docs(repo, host)
            reports = []
            for reviewer in ("security", "dx_ci", "whole_branch"):
                report = repo / f"reviews/{reviewer}.md"
                report.parent.mkdir(exist_ok=True)
                report.write_text(
                    "# Review: " + reviewer + "\n\nReviewed range: `" + base + ".." + head + "`\n\n"
                    "SPEC_COMPLIANCE=APPROVED\nCODE_QUALITY=APPROVED\n",
                    encoding="utf-8",
                )
                reports.append({
                    "reviewer": reviewer,
                    "path": report.relative_to(repo).as_posix(),
                    "reviewedCommit": head,
                    "reviewedRange": base + ".." + head,
                    "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                    "specVerdict": "APPROVED",
                    "codeQualityVerdict": "APPROVED",
                })
            subprocess.run(["git", "add", "reviews"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "independent reviews"], cwd=repo,
                           check=True, capture_output=True)
            manifest_document["reviewEvidence"]["reports"] = reports
            manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")
            complete_marker = "<!-- harness-delivery-status: locally_reviewed_sync_pending -->"
            for relative in ("README.md", "AGENTS.md", "docs/AUTONOMY.md", "docs/MEMORY.md"):
                (repo / relative).write_text(complete_marker + "\n", encoding="utf-8")
            self.runner.check_docs(repo, host)
            stages = (
                ({"independentReview": "complete", "github": "complete", "obsidian": "pending"},
                 "github_synced_obsidian_pending"),
                ({"independentReview": "complete", "github": "complete", "obsidian": "complete"},
                 "fully_synchronized"),
            )
            for external, status in stages:
                manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
                manifest_document["externalSync"] = external
                manifest_document["deliveryStatus"] = status
                manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")
                stage_marker = "<!-- harness-delivery-status: " + status + " -->"
                for relative in ("README.md", "AGENTS.md", "docs/AUTONOMY.md", "docs/MEMORY.md"):
                    (repo / relative).write_text(stage_marker + "\n", encoding="utf-8")
                self.runner.check_docs(repo, host)
            untracked = repo / "untracked-review.md"
            untracked.write_text((repo / reports[0]["path"]).read_text(encoding="utf-8"),
                                 encoding="utf-8")
            manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_document["reviewEvidence"]["reports"][0]["path"] = untracked.name
            manifest_document["reviewEvidence"]["reports"][0]["sha256"] = hashlib.sha256(
                untracked.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_docs(repo, host)
            manifest_document["reviewEvidence"]["reports"] = reports
            manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")
            plan.write_text(plan.read_text(encoding="utf-8").replace("- [ ] sync", "- [x] sync"),
                            encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_docs(repo, host)

    def test_host_config_rejects_fixture_plan_outside_authoritative_root(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "docs").mkdir()
            config = repo / "docs/HOST.json"
            config.write_text(json.dumps({
                "version": 1,
                "activePlan": "docs/superpowers/plans/active.md",
                "deliveryManifest": "docs/DELIVERY.json",
                "ledger": "docs/progress.md",
                "secretPolicy": self.secret_policy(),
                "fixture": {"path": "fixture.txt", "planPath": "plans/smoke.md",
                            "baseline": "baseline\n", "expected": "baseline\nok\n"},
                "documentation": {"statusMarkerPaths": ["README.md"]},
            }), encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.load_host_config(repo, config.relative_to(repo))

    def test_portable_copy_runs_wrapper_and_fake_autonomous_smoke(self):
        with tempfile.TemporaryDirectory(prefix="task8-portable-", dir=REPO.parents[2]) as directory:
            repo = Path(directory) / "portable"
            shutil.copytree(REPO / "tools/agent-harness", repo / "tools/agent-harness",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(REPO / "docs/agent", repo / "docs/agent")
            (repo / "scripts").mkdir()
            shutil.copy2(REPO / "scripts/agent-harness", repo / "scripts/agent-harness")
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo,
                           check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo,
                           check=True, capture_output=True)
            config = repo / "config/portable-host.json"
            config.parent.mkdir()
            config.write_text(json.dumps({
                "version": 1,
                "activePlan": "docs/superpowers/plans/portable.md",
                "deliveryManifest": "docs/agent/PORTABLE_DELIVERY.json",
                "ledger": "docs/agent/portable-progress.md",
                "secretPolicy": self.secret_policy(),
                "fixture": {"path": "fixtures/smoke.txt",
                            "planPath": "docs/superpowers/plans/smoke.md",
                            "baseline": "baseline\n", "expected": "baseline\nok\n"},
                "documentation": {"statusMarkerPaths": ["README.md", "docs/agent/AUTONOMY.md"]},
            }, sort_keys=True), encoding="utf-8")
            plan = repo / "docs/superpowers/plans/portable.md"
            plan.parent.mkdir(parents=True)
            plan.write_text("# Portable\n\n### Task 1: Core\n- [x] done\n\n"
                            "### Task 8: Delivery\n- [ ] sync\n", encoding="utf-8")
            (repo / "docs/superpowers/plans/smoke.md").write_text("# Fixture\n", encoding="utf-8")
            (repo / "fixtures").mkdir()
            (repo / "fixtures/smoke.txt").write_text("baseline\n", encoding="utf-8")
            marker = "<!-- harness-delivery-status: local_validated_sync_pending -->\n"
            (repo / "README.md").write_text(marker, encoding="utf-8")
            (repo / "docs/agent/AUTONOMY.md").write_text(marker, encoding="utf-8")
            (repo / "docs/agent/portable-progress.md").write_text(
                "Task 1: complete\nTask 8: in_progress\n", encoding="utf-8")
            (repo / "docs/agent/HARNESS_HOST.json").unlink()
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "portable host"], cwd=repo, check=True,
                           capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  text=True, capture_output=True).stdout.strip()
            manifest = repo / "docs/agent/PORTABLE_DELIVERY.json"
            manifest.write_text(json.dumps({
                "version": 1,
                "planPath": "docs/superpowers/plans/portable.md",
                "implementationCommit": head,
                "evidence": {"headCommit": head, "status": "passed", "commands": ["portable"]},
                "taskSteps": {"1": {"completed": [1], "pending": []},
                              "8": {"completed": [], "pending": [1]}},
                "deliveryStatus": "local_validated_sync_pending",
                "externalSync": {"independentReview": "pending", "github": "pending",
                                 "obsidian": "pending"},
                "reviewEvidence": {"requiredReviewers": ["security", "dx_ci", "whole_branch"],
                                   "reports": []},
            }, sort_keys=True), encoding="utf-8")
            subprocess.run(["git", "add", str(manifest.relative_to(repo))], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "portable manifest"], cwd=repo, check=True,
                           capture_output=True)
            checked = subprocess.run([
                str(repo / "scripts/agent-harness"), "--python",
                "tools/agent-harness/drills/run_drills.py", "--repo", str(repo),
                "--host-config", config.relative_to(repo).as_posix(), "--check-docs",
            ], cwd=repo, text=True, capture_output=True, check=False)
            self.assertEqual(checked.returncode, 0, checked.stderr or checked.stdout)
            self.assertIn("PASS plan-memory-drift", checked.stdout)
            result = subprocess.run([
                str(repo / "scripts/agent-harness"), "--python",
                "tools/agent-harness/drills/run_drills.py",
            ], cwd=repo, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertIn("PASSED success", result.stdout)
            plan.rename(plan.with_suffix(".missing"))
            missing = subprocess.run([
                str(repo / "scripts/agent-harness"), "--python",
                "tools/agent-harness/drills/run_drills.py", "--repo", str(repo),
                "--host-config", config.relative_to(repo).as_posix(), "--check-docs",
            ], cwd=repo, text=True, capture_output=True, check=False)
            self.assertEqual(missing.returncode, 2, missing.stderr or missing.stdout)

    def test_real_smoke_summary_keeps_sanitized_stop_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.runner._state(Path(directory), self.runner.RunStatus.PAUSED)
            state = replace(state, decisions=({
                "type": "transition",
                "summary": "Codex adapter timeout PASSWORD=fake-secret",
                "createdAt": "2026-10-10T00:00:00Z",
            },))
            detail = self.runner.real_smoke_detail(state, (), Path("history.md"))
        self.assertNotIn("fake-secret", detail)
        self.assertEqual(json.loads(detail)["stopReason"],
                         "Codex adapter timeout PASSWORD=[REDACTED]")

    def test_real_smoke_branch_names_are_unique_and_disposable(self):
        first = self.runner.real_smoke_branch_name()
        second = self.runner.real_smoke_branch_name()
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^drill/real-codex-smoke-[0-9a-f]{12}$")

    def test_real_smoke_infrastructure_failure_is_not_reported_as_pause(self):
        identity = ("a" * 40, b"")
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(self.runner, "_primary_identity", return_value=identity), \
                patch.object(self.runner, "_git", side_effect=OSError("PASSWORD=fake-secret")), \
                patch.object(self.runner, "_run", return_value=completed):
            result = self.runner.run_real_codex_smoke(REPO)
        self.assertEqual(result.status, "failed")
        self.assertNotIn("fake-secret", result.detail)


if __name__ == "__main__":
    unittest.main()
