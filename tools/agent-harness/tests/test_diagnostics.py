"""Failure identity, bounded repair decisions and safe diagnostic summaries."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_harness.diagnostics import (
    failure_fingerprint, normalize_failure, record_failure, redact_text, render_diagnostics,
)
from agent_harness.state import (
    AttemptState, GateEvidence, GateStatus, RunStatus, load_state,
    save_state_atomic,
)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.state = replace(
            load_state(Path(__file__).parent / "fixtures/valid_state.json"),
            worktree_path=self.root, status=RunStatus.VERIFYING,
            attempts=AttemptState(0, {}), last_failure_fingerprint=None,
            decisions=(),
        )
        self.gate = GateEvidence(
            "unit", GateStatus.FAILED, 1, self.now, self.now,
            Path("var/agent-harness/runs/example/evidence/gate-one/metadata.json"),
            self.state.head_commit,
        )
        self.state = replace(self.state, gates={"unit": self.gate})

    def test_normalizes_timestamps_ansi_ports_and_temporary_roots(self):
        variants = (
            "2026-10-10T08:30:01.123Z \x1b[31mERROR\x1b[0m "
            "/tmp/build-one/tests/test_api.py:42 ECONNREFUSED http://localhost:43123/api",
            "2026-10-11T09:41:02+08:00 ERROR "
            "/private/tmp/build-two/tests/test_api.py:42 ECONNREFUSED http://localhost:51987/api",
            "2026-10-12 10:42:03,456 ERROR "
            "/private/var/folders/ab/random/T/build-three/tests/test_api.py:42 "
            "ECONNREFUSED http://localhost:60001/api",
        )
        normalized = [normalize_failure(value) for value in variants]
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(normalized[0], normalized[2])
        self.assertIn("tests/test_api.py:42", normalized[0])
        self.assertIn("ECONNREFUSED", normalized[0])

    def test_fingerprint_retains_first_stable_error_not_wrapper_or_later_noise(self):
        first = "FAIL: test_login (tests.auth.AuthTests)\nAssertionError: denied"
        left = failure_fingerprint(1, "Server ready on port 43210\n" + first,
                                   "npm ERR! lifecycle failed")
        right = failure_fingerprint(1, "Server ready on port 51022\n" + first
                                    + "\nERROR: unrelated second failure",
                                    "npm ERR! lifecycle failed again")
        self.assertEqual(left, right)
        self.assertRegex(left, r"^[0-9a-f]{64}$")
        self.assertNotEqual(left, failure_fingerprint(2, first, ""))
        self.assertNotEqual(left, failure_fingerprint(1, first.replace("denied", "missing"), ""))
        self.assertNotEqual(left, failure_fingerprint(1, first.replace("test_login", "test_logout"), ""))

    def test_traceback_fingerprint_retains_stable_location_and_assertion(self):
        log = ('FAIL: test_login (tests.auth.AuthTests)\nTraceback (most recent call last):\n'
               '  File "src/auth.py", line 42, in test_login\n'
               '    self.assertEqual(status, 200)\nAssertionError: 401 != 200\n'
               '----------------------------------------------------------------------\nRan 8 tests in 1.32s')
        for changed in (log.replace("line 42", "line 43"), log.replace("401 != 200", "403 != 200")):
            self.assertNotEqual(failure_fingerprint(1, log, ""), failure_fingerprint(1, changed, ""))
        self.assertEqual(failure_fingerprint(1, log, ""),
                         failure_fingerprint(1, log.replace("1.32s", "8.94s"), ""))

    def test_stable_source_lines_error_codes_and_normal_paths_remain_distinct(self):
        log = "src/service.py:43210: error E123: expected 503 got 401"
        for changed in (log.replace("43210", "51022"), log.replace("E123", "E124"),
                        log.replace("503", "504"), log.replace("service", "auth")):
            with self.subTest(changed=changed):
                self.assertNotEqual(failure_fingerprint(1, log, ""),
                                    failure_fingerprint(1, changed, ""))

    def test_normalization_and_fingerprint_inputs_are_bounded(self):
        log = "ERROR: stable first failure\n" + "noise " * 200000
        self.assertLessEqual(len(normalize_failure(log)), 65536)
        self.assertEqual(failure_fingerprint(1, log, ""),
                         failure_fingerprint(1, "ERROR: stable first failure", ""))
        self.assertLessEqual(len(normalize_failure("port=1\n" * 9000)), 65536)

    def test_same_fingerprint_pauses_exactly_on_third_failure(self):
        state = self.state
        for number, code in ((1, "repair_allowed"), (2, "repair_allowed"),
                             (3, "same_failure_limit")):
            decision = record_failure(state, "a" * 64, str(number) * 64)
            self.assertEqual(decision.code, code)
            self.assertEqual(decision.should_pause, number == 3)
            self.assertEqual(decision.state.attempts.total, number)
            self.assertEqual(decision.state.attempts.by_fingerprint["a" * 64], number)
            self.assertEqual(decision.state.last_failure_fingerprint, "a" * 64)
            state = decision.state
        self.assertEqual(state.status, RunStatus.PAUSED)
        self.assertEqual(self.state.attempts.total, 0)

    def test_total_failures_pause_exactly_on_fifth_cycle(self):
        state = self.state
        for number in range(1, 6):
            decision = record_failure(state, str(number) * 64, "a" * 64)
            self.assertEqual(decision.code,
                             "total_attempt_limit" if number == 5 else "repair_allowed")
            self.assertEqual(decision.should_pause, number == 5)
            state = decision.state
        self.assertEqual(state.attempts.total, 5)
        self.assertEqual(state.status, RunStatus.PAUSED)

    def test_two_identical_cycles_pause_as_no_progress_after_disk_round_trip(self):
        first = record_failure(self.state, "a" * 64, "b" * 64)
        self.assertEqual(first.code, "repair_allowed")
        path = self.root / "state.json"
        save_state_atomic(path, first.state)
        state = load_state(path)
        # Times and HEAD are evidence bookkeeping, not a changed gate outcome.
        state = replace(state, gates={"unit": replace(self.gate, head_commit="d" * 40)})
        second = record_failure(state, "a" * 64, "b" * 64)
        self.assertEqual(second.code, "no_progress")
        self.assertTrue(second.should_pause)
        self.assertEqual(second.state.status, RunStatus.PAUSED)

    def test_each_progress_signal_resets_consecutive_detection(self):
        first = record_failure(self.state, "a" * 64, "b" * 64).state
        variants = (
            (first, "c" * 64, "b" * 64),
            (first, "a" * 64, "c" * 64),
            (replace(first, gates={"unit": replace(self.gate, exit_code=2)}), "a" * 64, "b" * 64),
            (replace(first, gates={"unit": replace(self.gate, status=GateStatus.TIMED_OUT)}),
             "a" * 64, "b" * 64),
        )
        for state, fingerprint, diff in variants:
            with self.subTest(fingerprint=fingerprint, diff=diff, gates=state.gates):
                self.assertEqual(record_failure(state, fingerprint, diff).code, "repair_allowed")

    def test_nonconsecutive_identical_cycles_do_not_trigger_no_progress(self):
        state = record_failure(self.state, "a" * 64, "b" * 64).state
        state = record_failure(state, "c" * 64, "d" * 64).state
        self.assertEqual(record_failure(state, "a" * 64, "b" * 64).code, "repair_allowed")

    def test_gate_order_does_not_count_as_progress(self):
        second_gate = replace(self.gate, gate_id="other", exit_code=2)
        state = replace(self.state, gates={"unit": self.gate, "other": second_gate})
        state = record_failure(state, "a" * 64, "b" * 64).state
        state = replace(state, gates={"other": second_gate, "unit": self.gate})
        self.assertEqual(record_failure(state, "a" * 64, "b" * 64).code, "no_progress")

    def test_terminal_states_cannot_consume_more_budget(self):
        for status in (RunStatus.PAUSED, RunStatus.BLOCKED, RunStatus.PLANNED):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    record_failure(replace(self.state, status=status), "a" * 64, "b" * 64)

    def test_invalid_digests_are_rejected_without_echoing_values(self):
        for value in ("", "secret-value", "a" * 63, "a" * 64 + "\n", None):
            for fingerprint, diff in ((value, "b" * 64), ("a" * 64, value)):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError) as caught:
                        record_failure(self.state, fingerprint, diff)
                    self.assertNotIn("secret-value", str(caught.exception))

    def test_redacts_secret_shapes_and_preserves_labels(self):
        cases = (
            ("https://fake-user:fake-pass@example.test/api", ("fake-user", "fake-pass"), "example.test/api"),
            ("Authorization: Bearer fake-bearer", ("fake-bearer",), "Authorization"),
            ("authorization: Basic ZmFrZTpmYWtl", ("ZmFrZTpmYWtl",), "authorization"),
            ('{"Authorization": "Bearer fake-json-auth", "status": "failed"}',
             ("fake-json-auth",), "status"),
            ("Cookie: session=fake-cookie; csrf=fake-csrf", ("fake-cookie", "fake-csrf"), "Cookie"),
            ("Set-Cookie: session=fake-set-cookie; HttpOnly", ("fake-set-cookie",), "Set-Cookie"),
            ('{"Cookie":"session=fake-json-cookie"}', ("fake-json-cookie",), "Cookie"),
            ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.ZmFrZXNpZ25hdHVyZQ",
             ("eyJhbGciOiJIUzI1NiJ9", "ZmFrZXNpZ25hdHVyZQ"), "jwt"),
            ("github ghp_" + "a" * 36, ("ghp_" + "a" * 36,), "github"),
            ("github github_pat_" + "a" * 22 + "_" + "b" * 59,
             ("github_pat_" + "a" * 22,), "github"),
            ("github ghs_" + "b" * 36, ("ghs_" + "b" * 36,), "github"),
            ("openai sk-" + "a" * 48, ("sk-" + "a" * 48,), "openai"),
            ("openai sk-proj-" + "x" * 48, ("sk-proj-" + "x" * 48,), "openai"),
            ("openai sk-svcacct-" + "z" * 48, ("sk-svcacct-" + "z" * 48,), "openai"),
            ("PASSWORD=fake-password", ("fake-password",), "PASSWORD"),
            ("DB_PASSWORD='fake password with spaces'", ("fake password with spaces",), "DB_PASSWORD"),
            ('{"TOKEN":"fake-json-token", "error":"denied"}', ("fake-json-token",), "error"),
            ("ACCESS_TOKEN=fake-access", ("fake-access",), "ACCESS_TOKEN"),
            ("TOKEN=fake-query&error=denied", ("fake-query",), "error=denied"),
        )
        for raw, secrets, label in cases:
            with self.subTest(label=label):
                redacted = redact_text(raw)
                self.assertIn(label, redacted)
                self.assertIn("[REDACTED]", redacted)
                for secret in secrets:
                    self.assertNotIn(secret, redacted)

    def test_redacts_pem_private_keys_including_unterminated_blocks(self):
        for kind in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"):
            for ending in ("\n-----END " + kind + "-----\nERROR: denied", ""):
                raw = "key:\n-----BEGIN " + kind + "-----\nfake-private-material" + ending
                with self.subTest(kind=kind, closed=bool(ending)):
                    result = redact_text(raw)
                    self.assertNotIn("fake-private-material", result)
                    self.assertIn("[REDACTED]", result)
                    if ending:
                        self.assertIn("ERROR: denied", result)

    def test_custom_secret_names_are_literal_case_insensitive_and_not_environment_reads(self):
        raw = 'SERVICE_CREDENTIAL="fake custom value" custom.key=fake-dot safe=visible'
        result = redact_text(raw, secret_names=(name for name in ("service_credential", "custom.key")))
        self.assertNotIn("fake custom value", result)
        self.assertNotIn("fake-dot", result)
        self.assertIn("SERVICE_CREDENTIAL", result)
        self.assertIn("custom.key", result)
        self.assertIn("safe=visible", result)
        self.assertEqual(redact_text("safe=visible"), "safe=visible")

    def test_redacts_multiline_quoted_values(self):
        for raw in ('PASSWORD="fake-first\nfake-second"\nERROR: denied',
                    'CUSTOM_CREDENTIAL="fake-first\nfake-second"\nERROR: denied',
                    '{"Authorization": "Bearer fake-first\nfake-second", "error":"denied"}'):
            result = redact_text(raw, ("CUSTOM_CREDENTIAL",))
            self.assertNotIn("fake-first", result)
            self.assertNotIn("fake-second", result)
            self.assertIn("denied", result)

    def test_unquoted_secret_with_spaces_stops_at_next_label(self):
        result = redact_text("ERROR: denied PASSWORD=fake secret with spaces status=failed")
        self.assertNotIn("fake", result)
        self.assertNotIn("secret with spaces", result)
        self.assertIn("ERROR: denied", result)
        self.assertIn("status=failed", result)

    def test_redacts_unterminated_quoted_values_with_dangling_escape(self):
        for raw in ('PASSWORD="fake-dangling\\', 'CUSTOM_CREDENTIAL="fake-dangling\\',
                    'Authorization: "fake-dangling\\'):
            with self.subTest(raw=raw):
                self.assertNotIn("fake-dangling", redact_text(raw, ("CUSTOM_CREDENTIAL",)))

    def test_redacts_unsigned_jwt(self):
        self.assertNotIn("eyJhbGciOiJub25lIn0", redact_text(
            "jwt eyJhbGciOiJub25lIn0.eyJzdWIiOiJmYWtlIn0."))

    def test_redacts_jwt_with_pretty_printed_json_header(self):
        token = "ewogICJhbGciOiAiSFMyNTYiCn0.eyJzdWIiOiJmYWtlIn0.ZmFrZXNpZ25hdHVyZQ"
        self.assertNotIn(token, redact_text("jwt " + token))

    def test_secret_name_validation_is_bounded_for_all_consumers(self):
        for names in (["x"] * 65, ["x" * 129], [""], [None], ["bad\nname"]):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    redact_text("", names)
                with self.assertRaises(ValueError):
                    render_diagnostics(self.state, [], self.root / "report.md", secret_names=iter(names))

        def unbounded_names():
            yield from ["x"] * 65
            raise RuntimeError("Consumer read past name limit")

        with self.assertRaises(ValueError):
            render_diagnostics(self.state, [], self.root / "report.md", secret_names=unbounded_names())

    def test_redacts_folded_headers_and_ansi_inside_sensitive_names(self):
        raw = ("Author\x1b[31mization\x1b[0m: Bearer fake-auth\r\n"
               " fake-folded-auth\r\nCookie: fake-cookie\r\n\tfake-folded-cookie\r\n"
               "ERROR: permission denied")
        result = redact_text(raw)
        for secret in ("fake-auth", "fake-folded-auth", "fake-cookie", "fake-folded-cookie"):
            self.assertNotIn(secret, result)
        self.assertIn("ERROR: permission denied", result)

    def test_redaction_is_bounded_and_never_releases_cut_secret_suffixes(self):
        for raw in ("PASSWORD=" + "secret-chunk" * 100000,
                    'TOKEN="' + "secret-chunk" * 100000,
                    "-----BEGIN PRIVATE KEY-----\n" + "secret-chunk" * 100000):
            result = redact_text(raw)
            self.assertLessEqual(len(result), 65536)
            self.assertNotIn("secret-chunk", result)

    def test_fingerprint_hides_secrets_and_remains_stable_when_credentials_rotate(self):
        for function in (normalize_failure, lambda text: failure_fingerprint(1, text, "")):
            self.assertEqual(function("ERROR: denied PASSWORD=fake-one"),
                             function("ERROR: denied PASSWORD=fake-two"))
        self.assertNotIn("fake-one", normalize_failure("ERROR: denied PASSWORD=fake-one"))

    def write_logs(self, stdout="", stderr=""):
        directory = self.root / self.gate.evidence_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdout.log").write_text(stdout)
        (directory / "stderr.log").write_text(stderr)
        return directory

    def test_paused_report_contains_resume_context_and_minimal_safe_error(self):
        self.write_logs("verbose-unrelated-output\n" * 1000,
                        "setup-noise\nERROR: tests/api.py:42 permission denied TOKEN=fake-log-secret\n"
                        "Authorization: Bearer fake-auth-secret\nCookie: fake-cookie-secret\n"
                        "irrelevant-tail\n" * 2)
        state = record_failure(self.state, "a" * 64, "b" * 64).state
        state = record_failure(state, "a" * 64, "b" * 64).state
        state = replace(state, milestone_title="Failure escalation PASSWORD=fake-title-secret",
                        decisions=state.decisions + ({"type": "repair", "summary":
                            "Adjusted timeout TOKEN=fake-decision-secret", "createdAt":
                            "2026-10-10T00:00:00Z"},))
        output = self.root / "diagnostics.md"
        render_diagnostics(state, [self.gate], output)
        report = output.read_text()
        for value in (state.plan_path.as_posix(), state.milestone_id, state.branch,
                      state.head_commit, state.changed_paths[0].as_posix(), "unit",
                      "a" * 64, "no_progress", "2", "Adjusted timeout",
                      "scripts/agent-harness resume",
                      "tests/api.py:42 permission denied"):
            self.assertIn(value, report)
        for secret in ("fake-log-secret", "fake-auth-secret", "fake-cookie-secret",
                       "fake-title-secret", "fake-decision-secret", "verbose-unrelated-output",
                       "irrelevant-tail", "setup-noise"):
            self.assertNotIn(secret, report)
        self.assertLess(len(report), 8000)
        self.assertNotIn("--run", report)

    def test_report_redacts_custom_names_in_logs_and_state(self):
        self.write_logs(stderr="ERROR: denied SERVICE_CREDENTIAL=fake-log-custom")
        state = replace(self.state, status=RunStatus.PAUSED,
                        milestone_title="SERVICE_CREDENTIAL=fake-state-custom")
        output = self.root / "diagnostics.md"
        render_diagnostics(state, [self.gate], output, secret_names=("SERVICE_CREDENTIAL",))
        self.assertNotIn("fake-log-custom", output.read_text())
        self.assertNotIn("fake-state-custom", output.read_text())

    def test_report_skips_passed_gates_and_keeps_timeout_and_missing_evidence_useful(self):
        self.write_logs(stderr="ERROR: this-passed-log-must-not-appear")
        passed = replace(self.gate, status=GateStatus.PASSED, exit_code=0)
        timeout = replace(self.gate, gate_id="timeout", status=GateStatus.TIMED_OUT,
                          exit_code=None, evidence_path=None)
        output = self.root / "diagnostics.md"
        render_diagnostics(replace(self.state, status=RunStatus.PAUSED), [passed, timeout], output)
        report = output.read_text()
        self.assertNotIn("this-passed-log-must-not-appear", report)
        self.assertIn("timeout", report)
        self.assertIn("timed_out", report)
        self.assertIn("unavailable", report)

    def test_report_never_follows_evidence_file_or_parent_symlinks(self):
        directory = self.write_logs()
        outside = self.root / "private.log"
        outside.write_text("ERROR: private-unrecognized-material")
        stderr = directory / "stderr.log"
        stderr.unlink()
        stderr.symlink_to(outside)
        output = self.root / "diagnostics.md"
        render_diagnostics(self.state, [self.gate], output)
        self.assertNotIn("private-unrecognized-material", output.read_text())
        self.assertIn("unavailable", output.read_text())
        parent_link = directory.parent / "gate-link"
        parent_link.symlink_to(directory, target_is_directory=True)
        linked = replace(self.gate, evidence_path=parent_link.relative_to(self.root) / "metadata.json")
        render_diagnostics(self.state, [linked], output)
        self.assertNotIn("private-unrecognized-material", output.read_text())
        self.assertIn("unavailable", output.read_text())

    def test_report_rejects_non_raw_evidence_and_bounds_large_logs(self):
        outside_raw = self.root / "private.log"
        outside_raw.write_text("ERROR: private-unrecognized-material")
        output = self.root / "diagnostics.md"
        render_diagnostics(self.state, [replace(self.gate, evidence_path=Path("private.log"))], output)
        self.assertNotIn("private-unrecognized-material", output.read_text())
        self.write_logs(stderr="ERROR: stable first failure\n" + "TOKEN=fake-large-secret\n" * 100000)
        render_diagnostics(self.state, [self.gate], output)
        self.assertIn("stable first failure", output.read_text())
        self.assertNotIn("fake-large-secret", output.read_text())
        self.assertLess(len(output.read_text()), 8000)

    def test_report_cannot_overwrite_raw_evidence_or_follow_output_symlink(self):
        directory = self.write_logs(stderr="ERROR: original evidence")
        raw = directory / "stderr.log"
        link = self.root / "diagnostics.md"
        link.symlink_to(raw)
        for output in (raw, link):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    render_diagnostics(self.state, [self.gate], output)
                self.assertEqual(raw.read_text(), "ERROR: original evidence")

    def test_report_write_is_atomic_and_keeps_old_report_on_replace_failure(self):
        output = self.root / "diagnostics.md"
        output.write_text("previous report")
        with patch("agent_harness.diagnostics.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                render_diagnostics(self.state, [], output)
        self.assertEqual(output.read_text(), "previous report")
        self.assertEqual(list(self.root.glob(".diagnostics.*")), [])


if __name__ == "__main__":
    unittest.main()
