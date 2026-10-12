"""Context Capsules are deterministic, provenance-bound and byte-budgeted."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.context_capsule import (  # noqa: E402
    build_context_capsule, extract_markdown_section, write_context_capsule_atomic,
)
from agent_harness.state import load_state  # noqa: E402


class ContextCapsuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        (self.repo / "docs/agent").mkdir(parents=True)
        (self.repo / "AGENTS.md").write_text(
            "# Rules\n\n## 实现与验证\nverify first\n\n## 安全与权限\nno secrets\n"
            "\n## 自治执行与停止条件\nstop on drift\n", encoding="utf-8")
        (self.repo / "docs/agent/PROJECT_MEMORY.md").write_text(
            "# Memory\n\n## 身份与权威入口\nrepository truth\n"
            "\n## 已确认边界与维护\nkeep boundaries\n", encoding="utf-8")
        (self.repo / "docs/agent/AUTONOMY.md").write_text(
            "# SOP\n\n## 修复预算与人工升级\nthree repeats stop\n", encoding="utf-8")
        self.state = replace(
            load_state(HARNESS / "tests/fixtures/valid_state.json"),
            worktree_path=self.repo,
        )

    def test_extracts_exact_markdown_section_and_rejects_ambiguous_heading(self):
        path = self.repo / "AGENTS.md"
        section = extract_markdown_section(path, "实现与验证")
        self.assertEqual(section, "## 实现与验证\nverify first\n")
        with self.assertRaises(ValueError):
            extract_markdown_section(path, "missing")
        duplicate = self.repo / "duplicate.md"
        duplicate.write_text("## Same\na\n## Same\nb\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            extract_markdown_section(duplicate, "Same")

    def test_stable_prefix_is_byte_identical_across_dynamic_run_state(self):
        first = build_context_capsule(self.repo, self.state, "Implement task")
        second = build_context_capsule(
            self.repo, replace(self.state, head_commit="f" * 40), "Different task"
        )
        self.assertEqual(first.stable_prefix.encode(), second.stable_prefix.encode())
        self.assertNotEqual(first.task_delta, second.task_delta)
        self.assertLessEqual(len(first.stable_prefix.encode("utf-8")), 12288)
        self.assertLessEqual(len(first.task_delta.encode("utf-8")), 8192)
        self.assertLessEqual(first.total_bytes, 24576)
        self.assertEqual([source.path for source in first.sources], sorted(
            source.path for source in first.sources
        ))

    def test_sources_bind_exact_ranges_digests_and_repository_paths(self):
        capsule = build_context_capsule(self.repo, self.state, "Implement task")
        for source in capsule.sources:
            self.assertFalse(Path(source.path).is_absolute())
            self.assertNotIn("..", Path(source.path).parts)
            lines = (self.repo / source.path).read_text(encoding="utf-8").splitlines(True)
            selected = "".join(lines[source.start_line - 1:source.end_line])
            self.assertEqual(hashlib.sha256(selected.encode()).hexdigest(), source.digest)
            self.assertEqual(len(selected.encode()), source.byte_count)

    def test_multibyte_budgets_fail_without_silent_truncation(self):
        with self.assertRaises(ValueError):
            build_context_capsule(self.repo, self.state, "测" * 3000)
        with self.assertRaises(ValueError):
            build_context_capsule(self.repo, self.state, "task", "错" * 1400)

    def test_atomic_write_persists_digest_bound_schema_document(self):
        capsule = build_context_capsule(self.repo, self.state, "Implement task", "ERROR: x")
        path = self.repo / "capsule.json"
        write_context_capsule_atomic(path, capsule)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["capsuleDigest"], capsule.capsule_digest)
        self.assertEqual(document["totalBytes"], capsule.total_bytes)
        self.assertEqual(document["diagnostic"], "ERROR: x")
        document["taskDelta"] += "tampered"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            write_context_capsule_atomic(path, capsule)


if __name__ == "__main__":
    unittest.main()
