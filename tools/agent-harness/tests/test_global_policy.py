"""Global token policy installation owns only explicit marker blocks."""

from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.global_policy import (  # noqa: E402
    GlobalPolicyError,
    install_global_policy,
)


class GlobalPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "tools/agent-harness/templates").mkdir(parents=True)
        (self.repo / "docs/agent").mkdir(parents=True)
        (self.repo / "tools/agent-harness/templates/global-token-rules.md").write_text(
            "- Prefer bounded summaries.\n", encoding="utf-8")
        (self.repo / "tools/agent-harness/templates/engineering-token-efficiency.md").write_text(
            "# Token method\n\nUse capsules.\n", encoding="utf-8")
        (self.repo / "docs/agent/MEMORY_PROJECTIONS.json").write_text(
            '{"version":1,"projections":[{"id":"progress","notePath":"Project/Progress.md","startMarker":"s","endMarker":"e"}]}',
            encoding="utf-8")
        (self.repo / "docs/agent/HARNESS_HOST.json").write_text(
            '{"expectedRepository":"owner/repo"}', encoding="utf-8")
        self.home = self.root / "codex"
        (self.home / "memories").mkdir(parents=True)
        (self.home / "AGENTS.md").write_text("human before\nhuman after\n", encoding="utf-8")

    def test_dry_run_shows_changes_without_writing(self):
        before = (self.home / "AGENTS.md").read_text()
        result = install_global_policy(self.repo, self.home, dry_run=True)
        self.assertIn("AGENTS.md", result.changed)
        self.assertIn("codex-token-efficiency:start", result.diff)
        self.assertEqual((self.home / "AGENTS.md").read_text(), before)
        self.assertFalse((self.home / "memories/engineering-token-efficiency.md").exists())

    def test_first_install_preserves_human_text_and_second_is_empty(self):
        first = install_global_policy(self.repo, self.home)
        self.assertEqual(len(first.changed), 3)
        agents = (self.home / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith("human before\nhuman after\n"))
        self.assertIn("Prefer bounded summaries", agents)
        second = install_global_policy(self.repo, self.home, dry_run=True)
        self.assertEqual(second.changed, ())
        self.assertEqual(second.diff, "")

    def test_template_update_replaces_block_only(self):
        install_global_policy(self.repo, self.home)
        agents = self.home / "AGENTS.md"
        agents.write_text("manual header\n" + agents.read_text(), encoding="utf-8")
        template = self.repo / "tools/agent-harness/templates/global-token-rules.md"
        template.write_text("- Updated rule.\n", encoding="utf-8")
        install_global_policy(self.repo, self.home)
        content = agents.read_text()
        self.assertTrue(content.startswith("manual header\n"))
        self.assertIn("Updated rule", content)
        self.assertNotIn("Prefer bounded", content)

    def test_missing_home_malformed_markers_and_symlink_escape_are_rejected(self):
        with self.assertRaises(GlobalPolicyError):
            install_global_policy(self.repo, self.root / "missing")
        agents = self.home / "AGENTS.md"
        agents.write_text("<!-- codex-token-efficiency:start -->\nbroken\n", encoding="utf-8")
        with self.assertRaises(GlobalPolicyError):
            install_global_policy(self.repo, self.home)
        agents.write_text("human\n", encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("private", encoding="utf-8")
        target = self.home / "memories/project-context-index.md"
        target.symlink_to(outside)
        with self.assertRaises(GlobalPolicyError):
            install_global_policy(self.repo, self.home)
        self.assertEqual(outside.read_text(), "private")


if __name__ == "__main__":
    unittest.main()
