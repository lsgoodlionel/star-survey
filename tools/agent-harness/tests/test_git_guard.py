"""Verify Git and filesystem boundaries against real temporary repositories."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import PathRule, PolicyConfig  # noqa: E402
from agent_harness.state import load_state  # noqa: E402
from agent_harness import git_guard  # noqa: E402
from agent_harness.git_guard import (  # noqa: E402
    GitGuardError, assert_worktree_isolated, capture_snapshot, changed_paths,
    classify_paths, validate_resume,
)


class GitRepoFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        self.git("init", "-b", "main", repo=self.main)
        self.git("config", "user.email", "harness@example.invalid", repo=self.main)
        self.git("config", "user.name", "Harness Test", repo=self.main)
        self.git("config", "commit.gpgsign", "false", repo=self.main)
        (self.main / "tracked.txt").write_text("initial\n")
        self.git("add", "--", "tracked.txt", repo=self.main)
        self.git("commit", "-m", "initial", repo=self.main)
        self.repo = self.root / "worktree"
        self.git("worktree", "add", "-b", "feat/run", str(self.repo), repo=self.main)
        document = json.loads((HARNESS / "tests/fixtures/valid_state.json").read_text())
        document.update(worktreePath=str(self.repo), branch="feat/run",
                        baseCommit=self.head(), headCommit=self.head(), changedPaths=[])
        state_path = self.root / "state.json"
        state_path.write_text(json.dumps(document))
        self.state = load_state(state_path)

    def git(self, *args, repo=None, check=True):
        return subprocess.run(["git", *args], cwd=repo or self.repo, text=True,
                              capture_output=True, check=check).stdout.rstrip("\n")

    def head(self):
        return self.git("rev-parse", "HEAD")

    def commit(self, path="tracked.txt", content="child\n", own=True):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", "--", path)
        message = "child"
        if own:
            message += "\n\nAgent-Run-Id: " + self.state.run_id
        self.git("commit", "-m", message)
        return self.head()

    def decision(self, state=None):
        return validate_resume(capture_snapshot(self.repo), state or self.state)


class GitGuardTests(GitRepoFixture):
    def test_clean_snapshot_and_exact_resume(self):
        snapshot = capture_snapshot(self.repo)
        self.assertEqual(snapshot.repo, self.repo)
        self.assertEqual(snapshot.branch, "feat/run")
        self.assertEqual(snapshot.head_commit, self.state.head_commit)
        self.assertEqual(snapshot.dirty_paths, ())
        self.assertEqual(snapshot.untracked_paths, ())
        result = validate_resume(snapshot, self.state)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "exact_match")
        self.assertFalse(result.invalidate_gates)

    def test_porcelain_v2_preserves_spaces_newlines_and_untracked_files(self):
        (self.repo / "tracked.txt").write_text("dirty\n")
        names = ("new file.txt", 'quote"tab\tline\n.txt', "$(touch SHOULD_NOT_EXIST)")
        for name in names:
            (self.repo / name).write_text("new\n")
        snapshot = capture_snapshot(self.repo)
        self.assertEqual(snapshot.dirty_paths, tuple(sorted(("tracked.txt", *names))))
        self.assertEqual(snapshot.untracked_paths, tuple(sorted(names)))
        self.assertFalse((self.repo / "SHOULD_NOT_EXIST").exists())

    def test_porcelain_rename_includes_both_protected_source_and_destination(self):
        self.git("mv", "tracked.txt", "renamed file.txt")
        snapshot = capture_snapshot(self.repo)
        self.assertEqual(snapshot.dirty_paths, ("renamed file.txt", "tracked.txt"))
        self.assertEqual(changed_paths(self.repo, self.state.base_commit),
                         ("renamed file.txt", "tracked.txt"))

    def test_porcelain_unmerged_entries_are_dirty(self):
        self.git("switch", "-c", "side")
        self.commit(content="side\n")
        self.git("switch", "feat/run")
        self.commit(content="ours\n")
        self.git("merge", "side", check=False)
        self.assertEqual(capture_snapshot(self.repo).dirty_paths, ("tracked.txt",))
        self.assertEqual(self.decision().reason, "dirty_drift")

    def test_diff_includes_committed_staged_unstaged_deleted_and_untracked(self):
        child = self.commit("committed.txt")
        (self.repo / "tracked.txt").unlink()
        (self.repo / "staged.txt").write_text("staged\n")
        self.git("add", "--", "staged.txt")
        (self.repo / "untracked.txt").write_text("new\n")
        self.assertEqual(changed_paths(self.repo, self.state.base_commit),
                         ("committed.txt", "staged.txt", "tracked.txt", "untracked.txt"))
        self.assertEqual(changed_paths(self.repo, self.state.base_commit, child),
                         ("committed.txt",))

    def test_committed_rename_reports_old_and_new_paths(self):
        self.git("mv", "tracked.txt", "new.txt")
        self.git("commit", "-m", "rename")
        self.assertEqual(changed_paths(self.repo, self.state.base_commit, "HEAD"),
                         ("new.txt", "tracked.txt"))

    def test_invalid_revisions_and_option_injection_are_explicit_errors(self):
        for revision in ("missing", "--output=owned", "HEAD;touch owned"):
            with self.subTest(revision=revision), self.assertRaises(GitGuardError):
                changed_paths(self.repo, revision)
        self.assertFalse((self.repo / "owned").exists())

    def test_non_repository_and_nested_directory_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        nested = self.repo / "nested"
        nested.mkdir()
        for repo in (outside, nested):
            with self.subTest(repo=repo), self.assertRaises(GitGuardError):
                capture_snapshot(repo)

    def test_only_linked_worktree_is_isolated(self):
        assert_worktree_isolated(self.repo)
        with self.assertRaisesRegex(GitGuardError, "shared"):
            assert_worktree_isolated(self.main)
        with self.assertRaisesRegex(GitGuardError, "shared"):
            validate_resume(capture_snapshot(self.main),
                            replace(self.state, worktree_path=self.main, branch="main"))

    def test_detached_and_unborn_head_are_explicit_errors(self):
        self.git("switch", "--detach")
        for operation in (capture_snapshot, assert_worktree_isolated):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(
                    GitGuardError, "detached"):
                operation(self.repo)
        unborn = self.root / "unborn"
        unborn.mkdir()
        self.git("init", "-b", "main", repo=unborn)
        with self.assertRaisesRegex(GitGuardError, "unborn"):
            capture_snapshot(unborn)

    def test_dirty_and_untracked_drift_refuse_resume_even_on_known_paths(self):
        for path in ("tracked.txt", "unrelated.txt"):
            with self.subTest(path=path):
                target = self.repo / path
                target.write_text("unknown content\n")
                state = replace(self.state, changed_paths=(Path(path),))
                result = self.decision(state)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "dirty_drift")
                if path == "tracked.txt":
                    target.write_text("initial\n")
                else:
                    target.unlink()

    def test_branch_and_worktree_identity_drift_refuse_resume(self):
        self.git("switch", "-c", "other")
        self.assertEqual(self.decision().reason, "branch_drift")
        self.assertEqual(self.decision(replace(self.state, worktree_path=self.main)).reason,
                         "worktree_drift")

    def test_same_run_child_invalidates_previous_gate_evidence(self):
        self.commit()
        result = self.decision(replace(self.state, changed_paths=(Path("tracked.txt"),)))
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "explainable_descendant")
        self.assertTrue(result.invalidate_gates)

    def test_multiple_owned_children_are_explainable(self):
        self.commit(content="first\n")
        self.commit(content="second\n")
        result = self.decision(replace(self.state, changed_paths=(Path("tracked.txt"),)))
        self.assertTrue(result.allowed)
        self.assertTrue(result.invalidate_gates)

    def test_ancestor_relation_alone_does_not_prove_run_ownership(self):
        self.commit(own=False)
        result = self.decision(replace(self.state, changed_paths=(Path("tracked.txt"),)))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "unexplained_head")

    def test_each_descendant_commit_requires_run_trailer_and_scope(self):
        self.commit("outside.txt")
        self.git("rm", "--", "outside.txt")
        self.git("commit", "-m", "remove\n\nAgent-Run-Id: " + self.state.run_id)
        result = self.decision(replace(self.state, changed_paths=(Path("tracked.txt"),)))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "unexplained_head")

    def test_foreign_intermediate_commit_cannot_be_hidden_by_owned_tip(self):
        self.commit(content="foreign\n", own=False)
        self.commit(content="owned\n")
        self.assertFalse(self.decision(
            replace(self.state, changed_paths=(Path("tracked.txt"),))).allowed)

    def test_run_marker_in_body_is_not_an_ownership_trailer(self):
        (self.repo / "tracked.txt").write_text("foreign\n")
        self.git("add", "--", "tracked.txt")
        self.git("commit", "-m", "body\n\nAgent-Run-Id: " + self.state.run_id
                 + "\n\nThis is ordinary prose.")
        self.assertFalse(self.decision(
            replace(self.state, changed_paths=(Path("tracked.txt"),))).allowed)

    def test_rewritten_history_and_rewind_refuse_resume(self):
        child = self.commit()
        state = replace(self.state, head_commit=child, changed_paths=(Path("tracked.txt"),))
        self.git("commit", "--amend", "-m", "rewritten")
        result = self.decision(state)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "history_drift")
        self.git("reset", "--hard", self.state.head_commit)
        self.assertEqual(self.decision(state).reason, "history_drift")

    def test_missing_commit_and_invalid_base_ancestry_are_ambiguous(self):
        for state in (replace(self.state, head_commit="f" * 40),
                      replace(self.state, base_commit="f" * 40)):
            with self.subTest(state=state):
                result = self.decision(state)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "ambiguous_ancestry")

    def test_non_ancestor_base_cannot_pass_even_when_head_matches(self):
        self.git("switch", "--orphan", "unrelated")
        unrelated = self.commit("other.txt")
        self.git("switch", "feat/run")
        result = self.decision(replace(self.state, base_commit=unrelated))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "ambiguous_ancestry")

    def test_shallow_history_is_ambiguous_even_with_exact_head(self):
        clone = self.root / "shallow"
        self.git("clone", "--depth=1", self.main.as_uri(), str(clone), repo=self.root)
        linked = self.root / "shallow-worktree"
        self.git("worktree", "add", "-b", "feat/shallow", str(linked), repo=clone)
        snapshot = capture_snapshot(linked)
        state = replace(self.state, worktree_path=linked, branch="feat/shallow")
        result = validate_resume(snapshot, state)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "ambiguous_ancestry")

    def test_owned_merge_cannot_hide_foreign_history(self):
        self.git("switch", "-c", "side")
        self.commit("foreign.txt", own=False)
        self.git("switch", "feat/run")
        self.commit()
        self.git("merge", "--no-ff", "side", "-m",
                 "merge\n\nAgent-Run-Id: " + self.state.run_id)
        state = replace(self.state, changed_paths=(Path("tracked.txt"), Path("foreign.txt")))
        self.assertFalse(self.decision(state).allowed)

    def test_duplicate_or_foreign_ownership_trailers_refuse_resume(self):
        for trailer in ("Agent-Run-Id: 22345678-1234-4234-8234-123456789abc",
                        "Agent-Run-Id: " + self.state.run_id
                        + "\nAgent-Run-Id: " + self.state.run_id):
            with self.subTest(trailer=trailer):
                self.git("commit", "--allow-empty", "-m", "child\n\n" + trailer)
                self.assertFalse(self.decision().allowed)
                self.git("reset", "--hard", self.state.head_commit)

    def test_stale_snapshot_cannot_authorize_new_worktree_changes(self):
        snapshot = capture_snapshot(self.repo)
        (self.repo / "new.txt").write_text("drift\n")
        result = validate_resume(snapshot, self.state)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "snapshot_drift")

    def test_exact_resume_rechecks_drift_after_real_ancestry_queries(self):
        snapshot = capture_snapshot(self.repo)
        ancestor = git_guard._ancestor

        def concurrent_edit(*args):
            result = ancestor(*args)
            (self.repo / "tracked.txt").write_text("concurrent edit\n")
            return result

        with patch.object(git_guard, "_ancestor", side_effect=concurrent_edit):
            result = validate_resume(snapshot, self.state)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "snapshot_drift")


class PathBoundaryTests(GitRepoFixture):
    def setUp(self):
        super().setUp()
        self.policy = PolicyConfig(1, (
            PathRule("review", ("**",), ("add", "modify", "delete"), "review_required"),
            PathRule("generated", ("protected/**",), ("modify",), "generated", ("generator",)),
            PathRule("approval", ("protected/**",), ("delete",), "approval_required"),
            PathRule("deny", ("protected/secret*",), ("add",), "deny"),
        ))

    def classify(self, *paths, policy=None):
        return classify_paths(self.repo, paths, policy or self.policy)

    def test_priority_is_independent_of_rule_order_and_operations_are_conservative(self):
        cases = (("protected/secret.txt", "deny"),
                 ("protected/auth.txt", "approval_required"),
                 ("ordinary.txt", "review_required"))
        for path, expected in cases:
            for rules in (self.policy.rules, tuple(reversed(self.policy.rules))):
                with self.subTest(path=path, rules=rules):
                    decision = self.classify(path, policy=PolicyConfig(1, rules))[0]
                    self.assertEqual(decision.action, expected)
        generated = PolicyConfig(1, (self.policy.rules[0], self.policy.rules[1]))
        decision = self.classify("protected/file.txt", policy=generated)[0]
        self.assertEqual(decision.action, "generated")
        self.assertEqual(decision.generator, ("generator",))

    def test_no_matching_rule_requires_review(self):
        result = self.classify("ordinary.txt", policy=PolicyConfig(1, (self.policy.rules[-1],)))[0]
        self.assertEqual(result.action, "review_required")

    def test_conflicting_generators_cannot_silently_choose_a_command(self):
        second = PathRule("other-generator", ("protected/**",), ("modify",),
                          "generated", ("other-generator",))
        policy = PolicyConfig(1, (self.policy.rules[0], self.policy.rules[1], second))
        result = self.classify("protected/generated.txt", policy=policy)[0]
        self.assertEqual(result.action, "deny")
        self.assertIsNone(result.generator)

    def test_new_and_deleted_protected_paths_keep_their_policy(self):
        path = self.repo / "protected/secret.txt"
        path.parent.mkdir()
        for exists in (True, False):
            if exists:
                path.write_text("fake\n")
            else:
                path.unlink()
            with self.subTest(exists=exists):
                self.assertEqual(self.classify("protected/secret.txt")[0].action, "deny")

    def test_absolute_parent_escape_and_invalid_paths_are_denied_before_rules(self):
        paths = ("../outside", "a/../../outside", str(self.repo / "inside"),
                 "//server/share", "C:/outside", "C:\\outside", "a\\b", "", ".", "a\x00b")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.classify(path)[0].action, "deny")

    def test_normalized_separators_dot_and_contained_parent_match_protection(self):
        for path in ("./protected//secret.txt", "ordinary/../protected/secret.txt",
                     "protected///secret.txt"):
            with self.subTest(path=path):
                result = self.classify(path)[0]
                self.assertEqual(result.path, "protected/secret.txt")
                self.assertEqual(result.action, "deny")

    def test_symlink_escape_broken_link_and_loop_are_denied(self):
        (self.repo / "escape").symlink_to(self.root, target_is_directory=True)
        (self.repo / "broken").symlink_to(self.root / "missing")
        (self.repo / "loop").symlink_to("loop")
        for path in ("escape/file", "escape/../worktree/tracked.txt", "broken", "loop/file"):
            with self.subTest(path=path):
                self.assertEqual(self.classify(path)[0].action, "deny")

    def test_internal_symlink_uses_target_and_lexical_protection(self):
        (self.repo / "protected").mkdir()
        (self.repo / "protected/secret.txt").write_text("fake\n")
        (self.repo / "alias").symlink_to("protected", target_is_directory=True)
        result = self.classify("alias/secret.txt")[0]
        self.assertEqual(result.path, "protected/secret.txt")
        self.assertEqual(result.action, "deny")
        (self.repo / "protected/secret-link").symlink_to("../tracked.txt")
        self.assertEqual(self.classify("protected/secret-link")[0].action, "deny")

    def test_case_alias_of_protected_symlink_cannot_bypass_lexical_rule(self):
        (self.repo / "protected").mkdir()
        (self.repo / "protected/secret-link").symlink_to("../tracked.txt")
        alias = self.repo / "PROTECTED/SECRET-LINK"
        result = self.classify("PROTECTED/SECRET-LINK")[0]
        if alias.exists():
            self.assertEqual(result.action, "deny")
        else:
            self.assertEqual(result.action, "review_required")

    def test_intermediate_protected_symlink_cannot_be_hidden_by_aliases(self):
        (self.repo / "protected").mkdir()
        (self.repo / "protected/secret-link").symlink_to("../tracked.txt")
        (self.repo / "directory-alias").symlink_to("protected", target_is_directory=True)
        (self.repo / "file-alias").symlink_to("protected/secret-link")
        for path in ("directory-alias/secret-link", "file-alias"):
            with self.subTest(path=path):
                self.assertEqual(self.classify(path)[0].action, "deny")

    def test_case_sensitive_patterns_and_actual_filesystem_case(self):
        self.assertEqual(self.classify("Protected/new.txt")[0].action, "review_required")
        (self.repo / "protected").mkdir()
        (self.repo / "protected/secret.txt").write_text("fake\n")
        alias = self.repo / "PROTECTED/SECRET.txt"
        result = self.classify("PROTECTED/SECRET.txt")[0]
        if alias.exists():
            self.assertEqual(result.path, "protected/secret.txt")
            self.assertEqual(result.action, "deny")
        else:
            self.assertEqual(result.action, "review_required")

    def test_unresolvable_non_directory_and_git_metadata_are_denied(self):
        for path in ("tracked.txt/child", ".git", ".git/config"):
            with self.subTest(path=path):
                self.assertEqual(self.classify(path)[0].action, "deny")

    def test_parent_after_a_file_is_unresolvable_even_when_it_normalizes_inside(self):
        for path in ("tracked.txt/../ordinary.txt", ".git/../ordinary.txt", "tracked.txt/."):
            with self.subTest(path=path):
                self.assertEqual(self.classify(path)[0].action, "deny")


if __name__ == "__main__":
    unittest.main()
