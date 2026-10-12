"""Obsidian projections preserve human text and fail closed on drift."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.memory_projection import (  # noqa: E402
    MemoryProjectionError,
    apply_memory_sync,
    configure_memory,
    load_projection_config,
    plan_memory_sync,
    render_projection,
)
from agent_harness.token_report import TokenReport, TokenUsage  # noqa: E402


class MemoryProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "docs/agent").mkdir(parents=True)
        (self.repo / "var/agent-harness").mkdir(parents=True)
        self.vault = self.root / "vault"
        self.config_path = self.repo / "docs/agent/MEMORY_PROJECTIONS.json"
        self.config_path.write_text(json.dumps({
            "version": 1,
            "projections": [{
                "id": "overview", "notePath": "Project/Overview.md",
                "startMarker": "<!-- codex:overview:start -->",
                "endMarker": "<!-- codex:overview:end -->",
            }],
        }), encoding="utf-8")
        self.state = SimpleNamespace(
            run_id="11111111-1111-4111-8111-111111111111",
            branch="feat/test", head_commit="a" * 40,
            status=SimpleNamespace(value="active"), milestone_id="m1",
            updated_at=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
            gates={}, decisions=(),
        )
        self.delivery = {"deliveryStatus": "local_validated_sync_pending",
                         "externalSync": {"repository": "pending"}}
        self.tokens = TokenReport(
            datetime(2026, 10, 12, tzinfo=timezone.utc),
            datetime(2026, 10, 13, tzinfo=timezone.utc),
            "docs/plan.md", 1, 2, 1, TokenUsage(100, 60, 40, 10, 2),
            TokenUsage(20, 10, 10, 2, 1),
        )

    def projection(self):
        return render_projection(self.state, self.delivery, self.tokens)

    def configured(self):
        configure_memory(self.repo, self.vault)
        return load_projection_config(self.repo)

    def test_marker_bounded_sync_preserves_manual_text_and_is_idempotent(self):
        self.vault.mkdir()
        note = self.vault / "Project/Overview.md"
        note.parent.mkdir()
        note.write_text("# Manual title\n\nHuman before.\n", encoding="utf-8")
        config = self.configured()
        first = apply_memory_sync(plan_memory_sync(config, self.projection()))
        content = note.read_text(encoding="utf-8")
        self.assertEqual(first.updated, ("overview",))
        self.assertIn("Human before.", content)
        self.assertIn("分支：`feat/test`", content)
        self.assertIn("<!-- codex:overview:start -->", content)
        second = apply_memory_sync(plan_memory_sync(load_projection_config(self.repo),
                                                    self.projection()))
        self.assertEqual(second.updated, ())
        self.assertEqual(note.read_text(encoding="utf-8"), content)

    def test_replaces_only_machine_block_and_preserves_surrounding_manual_text(self):
        self.vault.mkdir()
        note = self.vault / "Project/Overview.md"
        note.parent.mkdir()
        note.write_text("before\nafter\n", encoding="utf-8")
        config = self.configured()
        apply_memory_sync(plan_memory_sync(config, self.projection()))
        self.state.head_commit = "b" * 40
        apply_memory_sync(plan_memory_sync(load_projection_config(self.repo), self.projection()))
        content = note.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("before\nafter\n"))
        self.assertIn("`" + "b" * 40 + "`", content)
        self.assertNotIn("`" + "a" * 40 + "`", content)

    def test_manual_machine_block_edit_is_a_conflict(self):
        self.vault.mkdir()
        note = self.vault / "Project/Overview.md"
        note.parent.mkdir()
        note.write_text("manual\n", encoding="utf-8")
        config = self.configured()
        apply_memory_sync(plan_memory_sync(config, self.projection()))
        note.write_text(note.read_text(encoding="utf-8").replace("分支：", "人工改动："),
                        encoding="utf-8")
        with self.assertRaises(MemoryProjectionError):
            plan_memory_sync(load_projection_config(self.repo), self.projection())

    def test_unavailable_vault_is_journaled_and_later_replayed(self):
        config = self.configured()
        pending = apply_memory_sync(plan_memory_sync(config, self.projection()))
        self.assertEqual(pending.pending, ("overview",))
        host = json.loads((self.repo / "var/agent-harness/host-memory.json").read_text())
        self.assertEqual(host["projections"]["overview"]["status"], "pending")
        self.vault.mkdir()
        replayed = apply_memory_sync(plan_memory_sync(load_projection_config(self.repo),
                                                   self.projection()))
        self.assertEqual(replayed.updated, ("overview",))
        self.assertTrue((self.vault / "Project/Overview.md").is_file())

    def test_rejects_absolute_committed_paths_and_symlink_escape(self):
        document = json.loads(self.config_path.read_text())
        document["projections"][0]["notePath"] = str(self.root / "escape.md")
        self.config_path.write_text(json.dumps(document), encoding="utf-8")
        configure_memory(self.repo, self.vault)
        with self.assertRaises(MemoryProjectionError):
            load_projection_config(self.repo)
        document["projections"][0]["notePath"] = "Project/Overview.md"
        self.config_path.write_text(json.dumps(document), encoding="utf-8")
        self.vault.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.vault / "Project").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(MemoryProjectionError):
            plan_memory_sync(load_projection_config(self.repo), self.projection())

    def test_repository_projection_config_contains_no_host_absolute_path(self):
        configure_memory(self.repo, self.vault)
        committed = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.vault), committed)
        self.assertIn(str(self.vault),
                      (self.repo / "var/agent-harness/host-memory.json").read_text())


if __name__ == "__main__":
    unittest.main()
