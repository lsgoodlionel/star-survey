"""Exercise state trust boundaries, atomic writes and append-only event chains."""

import copy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.state import (  # noqa: E402
    AttemptState, GateEvidence, GateStatus, RunState, RunStatus,
    append_event, load_state, read_events, save_state_atomic, sha256_file,
    transition,
)


class StateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "state.json"
        self.events = self.root / "events.jsonl"
        self.document = json.loads(
            (HARNESS / "tests/fixtures/valid_state.json").read_text(encoding="utf-8")
        )

    def write_state(self, document=None):
        self.path.write_text(json.dumps(
            self.document if document is None else document
        ), encoding="utf-8")
        return self.path

    def state(self):
        return load_state(self.write_state())

    def test_load_and_save_match_wire_schema(self):
        state = self.state()
        self.assertIsInstance(state, RunState)
        self.assertIsInstance(state.attempts, AttemptState)
        self.assertIsInstance(state.gates["unit"], GateEvidence)
        self.assertEqual(state.status, RunStatus.PLANNED)
        self.assertEqual(state.gates["unit"].status, GateStatus.PASSED)
        self.assertEqual(state.plan_path, Path(self.document["planPath"]))
        self.assertEqual(state.gates["unit"].evidence_path,
                         Path(self.document["gates"]["unit"]["evidencePath"]))
        save_state_atomic(self.path, state)
        self.assertEqual(json.loads(self.path.read_text()), self.document)
        self.assertEqual(load_state(self.path), state)

    def test_models_are_deeply_immutable(self):
        state = self.state()
        with self.assertRaises(FrozenInstanceError):
            state.status = RunStatus.ACTIVE
        with self.assertRaises(FrozenInstanceError):
            state.gates["unit"].exit_code = 1
        with self.assertRaises(TypeError):
            state.gates["other"] = state.gates["unit"]
        with self.assertRaises(TypeError):
            state.attempts.by_fingerprint["d" * 64] = 2
        with self.assertRaises(TypeError):
            state.decisions[0]["summary"] = "changed"
        gates, decisions, fingerprints = dict(state.gates), [dict(state.decisions[0])], {"d" * 64: 1}
        isolated = replace(state, gates=gates, decisions=decisions,
                           attempts=AttemptState(1, fingerprints))
        gates.clear()
        decisions[0]["summary"] = "changed"
        fingerprints.clear()
        self.assertIn("unit", isolated.gates)
        self.assertEqual(isolated.decisions[0]["summary"], "Task 2 approved")
        self.assertEqual(isolated.attempts.by_fingerprint["d" * 64], 1)

    def test_exact_statuses_and_transition_graph(self):
        self.assertEqual({status.value for status in RunStatus}, {
            "planned", "active", "verifying", "repairing", "completed", "paused", "blocked",
        })
        self.assertEqual({status.value for status in GateStatus}, {
            "pending", "running", "passed", "failed", "timed_out",
        })
        allowed = {
            "planned": {"active", "paused", "blocked"},
            "active": {"verifying", "paused", "blocked"},
            "verifying": {"repairing", "completed", "paused", "blocked"},
            "repairing": {"verifying", "paused", "blocked"},
            "completed": set(), "paused": set(), "blocked": set(),
        }
        original = self.state()
        for source in RunStatus:
            for target in RunStatus:
                with self.subTest(source=source.value, target=target.value):
                    state = replace(original, status=source)
                    if target.value in allowed[source.value]:
                        result = transition(state, target, "Approved transition")
                        self.assertIsNot(result, state)
                        self.assertEqual(result.status, target)
                        self.assertEqual(state.status, source)
                        self.assertGreater(result.updated_at, state.updated_at)
                        self.assertEqual(result.decisions[-1]["summary"], "Approved transition")
                    else:
                        with self.assertRaises(ValueError):
                            transition(state, target, "Illegal transition")

    def test_transition_rejects_unknown_target_and_empty_reason(self):
        for target, reason in (("unknown", "reason"), (RunStatus.ACTIVE, ""),
                               (RunStatus.ACTIVE, "  "), (RunStatus.ACTIVE, None)):
            with self.subTest(target=target, reason=reason):
                with self.assertRaises(ValueError):
                    transition(self.state(), target, reason)

    def test_completed_requires_passed_gates_at_current_head(self):
        cases = []
        document = copy.deepcopy(self.document)
        document["gates"] = {}
        cases.append(document)
        for field, value in (("status", "failed"), ("headCommit", "b" * 40),
                             ("gateId", "other")):
            document = copy.deepcopy(self.document)
            document["gates"]["unit"][field] = value
            cases.append(document)
        for document in cases:
            document["status"] = "verifying"
            with self.subTest(gates=document["gates"]):
                state = load_state(self.write_state(document))
                with self.assertRaises(ValueError):
                    transition(state, RunStatus.COMPLETED, "Must not complete")

    def test_missing_required_fields_are_rejected_at_every_level(self):
        for section in (None, "attempts", "gate", "decision"):
            original = (self.document if section is None else
                        self.document["attempts"] if section == "attempts" else
                        self.document["gates"]["unit"] if section == "gate" else
                        self.document["decisions"][0])
            for field in original:
                document = copy.deepcopy(self.document)
                target = (document if section is None else
                          document["attempts"] if section == "attempts" else
                          document["gates"]["unit"] if section == "gate" else
                          document["decisions"][0])
                del target[field]
                with self.subTest(section=section, field=field):
                    with self.assertRaises(ValueError):
                        load_state(self.write_state(document))

    def test_unknown_fields_versions_and_invalid_values_fail_closed(self):
        mutations = [
            (("schemaVersion",), 2), (("schemaVersion",), True),
            (("runtime",), {}), (("attempts", "extra"), 0),
            (("gates", "unit", "extra"), 0), (("decisions", 0, "extra"), 0),
            (("status",), "done"), (("attempts", "total"), -1),
            (("attempts", "total"), True), (("attempts", "byFingerprint"), {"bad": 1}),
            (("attempts", "byFingerprint"), {"d" * 64: False}),
            (("planSha256",), "bad"), (("headCommit",), "bad"),
            (("runId",), "bad"), (("worktreePath",), "relative"),
            (("lastFailureFingerprint",), "bad"), (("branch",), ""),
            (("requiredGates",), ["unit", "unit"]), (("requiredGates",), "unit"),
            (("changedPaths",), ["same", "same"]), (("createdAt",), "2026-02-30T00:00:00Z"),
            (("updatedAt",), "2026-10-10T00:00:00+00:00"),
            (("gates", "unit", "status"), "done"),
            (("gates", "unit", "exitCode"), False),
            (("gates", "unit", "exitCode"), 1),
            (("gates", "unit", "startedAt"), None),
            (("gates", "unit", "endedAt"), None),
            (("gates", "unit", "evidencePath"), None),
        ]
        for path, value in mutations:
            document = copy.deepcopy(self.document)
            target = document
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.subTest(path=path, value=value):
                with self.assertRaises(ValueError):
                    load_state(self.write_state(document))

    def test_repository_relative_paths_reject_absolute_and_traversal(self):
        for value in ("/absolute", "../escape", "nested/../escape", "C:/escape", "a\\b", "a\x00b", ""):
            for field in ("planPath", "changedPaths", "evidencePath"):
                document = copy.deepcopy(self.document)
                if field == "evidencePath":
                    document["gates"]["unit"][field] = value
                else:
                    document[field] = [value] if field == "changedPaths" else value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        load_state(self.write_state(document))

    def test_nullable_gate_fields_and_dynamic_fingerprints_round_trip(self):
        self.document["gates"]["unit"].update(
            status="pending", exitCode=None, startedAt=None, endedAt=None, evidencePath=None
        )
        self.document["attempts"]["byFingerprint"]["e" * 64] = 2
        self.document["lastFailureFingerprint"] = None
        state = self.state()
        save_state_atomic(self.path, state)
        self.assertEqual(json.loads(self.path.read_text()), self.document)

    def test_malformed_state_json_is_rejected(self):
        for content in (b"{", b"[]", b"\xff", b'{"schemaVersion":1,"schemaVersion":1}',
                        b'{"schemaVersion":NaN}'):
            self.path.write_bytes(content)
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    load_state(self.path)

    def test_uuid_format_rejects_trailing_newline(self):
        self.document["runId"] += "\n"
        with self.assertRaises(ValueError):
            load_state(self.write_state())

    def test_timestamp_serialization_normalizes_aware_datetime_to_utc_z(self):
        state = replace(self.state(), updated_at=datetime(
            2026, 10, 10, 8, 30, tzinfo=timezone(timedelta(hours=8))
        ))
        save_state_atomic(self.path, state)
        self.assertEqual(json.loads(self.path.read_text())["updatedAt"], "2026-10-10T00:30:00Z")
        with self.assertRaises(ValueError):
            save_state_atomic(self.path, replace(state, updated_at=datetime(2026, 10, 10)))

    def test_atomic_save_preserves_previous_state_when_replace_fails(self):
        state = self.state()
        old_bytes = self.path.read_bytes()
        with patch("agent_harness.state.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_state_atomic(self.path, transition(state, RunStatus.ACTIVE, "Start"))
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_atomic_save_preserves_previous_state_when_fsync_fails(self):
        state = self.state()
        old_bytes = self.path.read_bytes()
        with patch("agent_harness.state.os.fsync", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                save_state_atomic(self.path, transition(state, RunStatus.ACTIVE, "Start"))
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_atomic_save_flushes_and_syncs_same_directory_before_replace(self):
        state = self.state()
        real_fsync, real_replace = os.fsync, os.replace
        observed = []

        def sync(fd):
            self.assertGreater(os.fstat(fd).st_size, 0)
            observed.append("fsync")
            real_fsync(fd)

        def replace_file(source, destination):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(Path(destination), self.path)
            self.assertEqual(json.loads(Path(source).read_text()), self.document)
            self.assertEqual(observed, ["fsync"])
            real_replace(source, destination)

        with patch("agent_harness.state.os.fsync", side_effect=sync), \
                patch("agent_harness.state.os.replace", side_effect=replace_file):
            save_state_atomic(self.path, state)
        self.assertEqual(load_state(self.path), state)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_sha256_file_uses_raw_bytes(self):
        self.path.write_bytes(b"abc")
        self.assertEqual(sha256_file(self.path),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_event_append_preserves_prefix_and_uses_canonical_digest(self):
        first = append_event(self.events, "started", {"b": 2, "a": {"x": [1, True, None]}})
        prefix = self.events.read_bytes()
        second = append_event(self.events, "verified", {"result": "passed"})
        self.assertTrue(self.events.read_bytes().startswith(prefix))
        records = read_events(self.events)
        self.assertEqual(len(records), 2)
        self.assertIsNone(records[0]["previousDigest"])
        self.assertEqual(records[1]["previousDigest"], first)
        self.assertEqual(records[1]["digest"], second)
        self.assertEqual(records[0]["type"], "started")
        for record in records:
            body = {key: value for key, value in record.items() if key != "digest"}
            expected = hashlib.sha256(json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")).hexdigest()
            self.assertEqual(record["digest"], expected)
            self.assertTrue(record["createdAt"].endswith("Z"))

    def test_event_chain_detects_modified_middle_record(self):
        for number in range(3):
            append_event(self.events, "step", {"number": number})
        lines = self.events.read_text().splitlines()
        record = json.loads(lines[1])
        record["payload"]["number"] = 99
        lines[1] = json.dumps(record)
        self.events.write_text("\n".join(lines) + "\n")
        with self.assertRaises(ValueError):
            read_events(self.events)
        old_bytes = self.events.read_bytes()
        with self.assertRaises(ValueError):
            append_event(self.events, "next", {})
        self.assertEqual(self.events.read_bytes(), old_bytes)

    def test_event_chain_rejects_rehashed_middle_record_and_removed_record(self):
        for number in range(3):
            append_event(self.events, "step", {"number": number})
        original = self.events.read_text().splitlines()
        record = json.loads(original[1])
        record["payload"]["number"] = 99
        body = {key: value for key, value in record.items() if key != "digest"}
        record["digest"] = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()).hexdigest()
        for lines in ([original[0], json.dumps(record), original[2]],
                      [original[0], original[2]], list(reversed(original))):
            self.events.write_text("\n".join(lines) + "\n")
            with self.assertRaises(ValueError):
                read_events(self.events)

    def test_event_reader_rejects_truncated_final_line(self):
        append_event(self.events, "started", {})
        append_event(self.events, "next", {"x": 1})
        original = self.events.read_bytes()
        for truncated in (original[:-1], original[:-8]):
            self.events.write_bytes(truncated)
            with self.assertRaises(ValueError):
                read_events(self.events)
            with self.assertRaises(ValueError):
                append_event(self.events, "next", {})
            self.assertEqual(self.events.read_bytes(), truncated)

    def test_event_reader_rejects_malformed_records(self):
        digest = append_event(self.events, "started", {})
        original = json.loads(self.events.read_text())
        variants = [b"\n", b"[]\n", b"{\n", b"\xff\n",
                    b'{"type":"x","type":"y"}\n', b'{"payload":NaN}\n']
        for field, value in (("digest", "bad"), ("previousDigest", digest),
                             ("type", ""), ("payload", []), ("extra", 1),
                             ("createdAt", "2026-02-30T00:00:00Z")):
            record = dict(original)
            record[field] = value
            if field != "digest":
                body = {key: item for key, item in record.items() if key != "digest"}
                record["digest"] = hashlib.sha256(json.dumps(
                    body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()).hexdigest()
            variants.append((json.dumps(record) + "\n").encode())
        for field in original:
            record = dict(original)
            del record[field]
            variants.append((json.dumps(record) + "\n").encode())
        for content in variants:
            self.events.write_bytes(content)
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    read_events(self.events)

    def test_invalid_event_payload_does_not_create_or_modify_log(self):
        for payload in ({"x": float("nan")}, {"x": object()}, {1: "non-string key"},
                        {"x": {1: "nested non-string key"}}, [], {"x": float("inf")}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    append_event(self.events, "started", payload)
                self.assertFalse(self.events.exists())
        for event_type in ("", "  ", None):
            with self.assertRaises(ValueError):
                append_event(self.events, event_type, {})
        append_event(self.events, "started", {})
        old_bytes = self.events.read_bytes()
        with self.assertRaises(ValueError):
            append_event(self.events, "next", {"x": float("nan")})
        self.assertEqual(self.events.read_bytes(), old_bytes)

    def test_empty_and_missing_event_logs(self):
        self.assertEqual(read_events(self.events), [])
        self.events.touch()
        self.assertEqual(read_events(self.events), [])


if __name__ == "__main__":
    unittest.main()
