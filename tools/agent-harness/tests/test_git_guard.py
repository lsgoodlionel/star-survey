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

from agent_harness.config import HistoryBoundaryPolicy, PathRule, PolicyConfig  # noqa: E402
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

    def git_bytes(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                              check=True).stdout

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
    def test_cr_crlf_and_lf_names_remain_distinct_in_status_and_diff(self):
        names = ("carriage\rreturn.txt", "carriage\r\nreturn.txt", "carriage\nreturn.txt")
        for name in names:
            (self.repo / name).write_text("fake\n")
        snapshot = capture_snapshot(self.repo)
        with self.subTest(query="status"):
            self.assertEqual(snapshot.dirty_paths, tuple(sorted(names)))
            self.assertEqual(snapshot.untracked_paths, tuple(sorted(names)))
        self.git("add", "--", *names)
        self.git("commit", "-m", "filenames")
        with self.subTest(query="diff"):
            self.assertEqual(changed_paths(self.repo, self.state.head_commit, "HEAD"),
                             tuple(sorted(names)))

    def test_cr_and_crlf_renames_preserve_both_endpoints_and_scope(self):
        source, target = "old\rname.txt", "new\r\nname.txt"
        old = self.commit(source)
        state = replace(self.state, base_commit=old, head_commit=old)
        self.git("mv", source, target)
        expected = (target, source)
        with self.subTest(query="porcelain rename"):
            self.assertEqual(capture_snapshot(self.repo).dirty_paths, expected)
        self.git("commit", "-m", "rename\n\nAgent-Run-Id: " + state.run_id)
        with self.subTest(query="committed rename"):
            self.assertEqual(changed_paths(self.repo, old, "HEAD"), expected)
        wrong_scope = replace(state, changed_paths=(Path("old\nname.txt"), Path("new\nname.txt")))
        with self.subTest(query="wrong scope"):
            self.assertFalse(self.decision(wrong_scope).allowed)
        correct_scope = replace(state, changed_paths=(Path(source), Path(target)))
        with self.subTest(query="correct scope"):
            self.assertTrue(self.decision(correct_scope).allowed)

    def test_cr_descendant_cannot_borrow_an_lf_scope(self):
        self.commit("carriage\rreturn.txt")
        wrong = replace(self.state, changed_paths=(Path("carriage\nreturn.txt"),))
        with self.subTest(scope="LF"):
            self.assertFalse(self.decision(wrong).allowed)
        correct = replace(self.state, changed_paths=(Path("carriage\rreturn.txt"),))
        with self.subTest(scope="CR"):
            self.assertTrue(self.decision(correct).allowed)

    def test_assume_unchanged_and_skip_worktree_are_refused_without_index_mutation(self):
        state = replace(self.state, gates={
            "unit": replace(self.state.gates["unit"], head_commit=self.state.head_commit),
        })
        index = Path(self.git("rev-parse", "--git-path", "index"))
        if not index.is_absolute():
            index = self.repo / index
        for flag in ("--assume-unchanged", "--skip-worktree"):
            self.git("update-index", flag, "--", "tracked.txt")
            for dirty in (False, True):
                (self.repo / "tracked.txt").write_text("unknown\n" if dirty else "initial\n")
                before = index.read_bytes()
                tags = self.git_bytes("ls-files", "-v", "-z")
                self.assertIn(b"tracked.txt\x00", tags)
                with self.subTest(flag=flag, dirty=dirty), self.assertRaisesRegex(
                        GitGuardError, "index"):
                    self.decision(state)
                self.assertEqual(index.read_bytes(), before)
                self.assertEqual(self.git_bytes("ls-files", "-v", "-z"), tags)
                self.assertEqual(state.gates["unit"].head_commit, state.head_commit)
            self.git("update-index", "--no-assume-unchanged", "--", "tracked.txt")
            self.git("update-index", "--no-skip-worktree", "--", "tracked.txt")
            (self.repo / "tracked.txt").write_text("initial\n")
        self.assertTrue(self.decision().allowed)

    def test_ignore_submodules_configuration_cannot_hide_committed_gitlink_scope(self):
        first = self.state.head_commit
        second = self.commit()
        self.git("update-index", "--add", "--cacheinfo", "160000," + first + ",vendor")
        (self.repo / "vendor").mkdir()
        self.git("commit", "-m", "gitlink baseline")
        base = self.head()
        state = replace(self.state, base_commit=base, head_commit=base)
        self.git("update-index", "--cacheinfo", "160000," + second + ",vendor")
        self.git("commit", "-m", "gitlink child\n\nAgent-Run-Id: " + state.run_id)
        self.assertEqual(capture_snapshot(self.repo).dirty_paths, ())
        for configured in (False, True):
            if configured:
                self.git("config", "diff.ignoreSubmodules", "all")
            with self.subTest(configured=configured, query="diff"):
                self.assertEqual(changed_paths(self.repo, base, "HEAD"), ("vendor",))
            with self.subTest(configured=configured, query="outside scope"):
                self.assertFalse(self.decision(state).allowed)
            with self.subTest(configured=configured, query="inside scope"):
                self.assertTrue(self.decision(replace(state, changed_paths=(Path("vendor"),))).allowed)

    def test_duplicate_empty_run_trailers_are_not_discarded(self):
        identifier = "Agent-Run-Id: " + self.state.run_id
        blocks = (identifier + "\nAgent-Run-Id:",
                  "Agent-Run-Id:\n" + identifier,
                  identifier + "\nAgent-Run-Id: \t")
        for block in blocks:
            self.git("commit", "--allow-empty", "-m", "child\n\n" + block)
            with self.subTest(block=block):
                self.assertFalse(self.decision().allowed)
            self.git("reset", "--hard", self.state.head_commit)

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

    def test_exact_host_trusted_shallow_boundary_allows_proven_ancestry(self):
        (self.main / "tracked.txt").write_text("main child\n")
        self.git("add", "--", "tracked.txt", repo=self.main)
        self.git("commit", "-m", "main child", repo=self.main)
        clone = self.root / "trusted-shallow"
        self.git("clone", "--depth=2", self.main.as_uri(), str(clone), repo=self.root)
        linked = self.root / "trusted-shallow-worktree"
        self.git("worktree", "add", "-b", "feat/trusted-shallow", str(linked), repo=clone)
        shallow = Path(self.git("rev-parse", "--git-path", "shallow", repo=linked))
        boundary = shallow.read_text(encoding="ascii").strip()
        snapshot = capture_snapshot(linked)
        state = replace(self.state, worktree_path=linked, branch="feat/trusted-shallow",
                        base_commit=boundary, head_commit=snapshot.head_commit)
        policy = HistoryBoundaryPolicy((boundary,))
        result = validate_resume(snapshot, state, policy)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "exact_match")

    def test_host_trusted_shallow_boundary_requires_exact_actual_set(self):
        clone = self.root / "changed-shallow"
        self.git("clone", "--depth=1", self.main.as_uri(), str(clone), repo=self.root)
        linked = self.root / "changed-shallow-worktree"
        self.git("worktree", "add", "-b", "feat/changed-shallow", str(linked), repo=clone)
        snapshot = capture_snapshot(linked)
        state = replace(self.state, worktree_path=linked, branch="feat/changed-shallow",
                        base_commit=snapshot.head_commit, head_commit=snapshot.head_commit)
        for allowed in (("f" * 40,), (snapshot.head_commit, "f" * 40)):
            with self.subTest(allowed=allowed):
                result = validate_resume(snapshot, state, HistoryBoundaryPolicy(allowed))
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "ambiguous_ancestry")

    def test_host_trusted_shallow_boundary_must_be_head_ancestor(self):
        clone = self.root / "unrelated-shallow"
        self.git("clone", "--depth=1", self.main.as_uri(), str(clone), repo=self.root)
        self.git("config", "user.email", "harness@example.invalid", repo=clone)
        self.git("config", "user.name", "Harness Test", repo=clone)
        self.git("switch", "--orphan", "unrelated", repo=clone)
        (clone / "tracked.txt").unlink(missing_ok=True)
        (clone / "unrelated.txt").write_text("unrelated\n")
        self.git("add", "unrelated.txt", repo=clone)
        self.git("commit", "-m", "unrelated", repo=clone)
        unrelated = self.git("rev-parse", "HEAD", repo=clone)
        self.git("switch", "main", repo=clone)
        shallow = Path(self.git("rev-parse", "--git-path", "shallow", repo=clone))
        if not shallow.is_absolute():
            shallow = clone / shallow
        shallow.write_text(unrelated + "\n", encoding="ascii")
        linked = self.root / "unrelated-shallow-worktree"
        self.git("worktree", "add", "-b", "feat/unrelated-shallow", str(linked), repo=clone)
        snapshot = capture_snapshot(linked)
        state = replace(self.state, worktree_path=linked, branch="feat/unrelated-shallow",
                        base_commit=snapshot.head_commit, head_commit=snapshot.head_commit)
        result = validate_resume(snapshot, state, HistoryBoundaryPolicy((unrelated,)))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "ambiguous_ancestry")

    def test_disconnected_host_boundary_requires_explicit_trusted_head_ref(self):
        self.git("switch", "--orphan", "upstream", repo=self.main)
        (self.main / "tracked.txt").unlink(missing_ok=True)
        (self.main / "upstream.txt").write_text("upstream\n")
        self.git("add", "upstream.txt", repo=self.main)
        self.git("commit", "-m", "upstream boundary", repo=self.main)
        boundary = self.git("rev-parse", "HEAD", repo=self.main)
        self.git("update-ref", "refs/remotes/upstream/master", boundary, repo=self.main)
        self.git("switch", "main", repo=self.main)
        shallow = Path(self.git("rev-parse", "--git-path", "shallow", repo=self.repo))
        shallow.write_text(boundary + "\n", encoding="ascii")
        snapshot = capture_snapshot(self.repo)
        rejected = validate_resume(snapshot, self.state, HistoryBoundaryPolicy((boundary,)))
        self.assertFalse(rejected.allowed)
        policy = HistoryBoundaryPolicy((boundary,), ("refs/remotes/upstream/master",))
        result = validate_resume(snapshot, self.state, policy)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "exact_match")

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

    def nested_directory_alias(self):
        target = self.repo / "safe/sub/file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("fake\n")
        (self.repo / "alias").symlink_to("safe", target_is_directory=True)
        (self.repo / "outer").symlink_to("alias/sub", target_is_directory=True)
        self.assertEqual((self.repo / "outer/file.txt").resolve(strict=True), target)
        return target

    def test_nested_alias_ignores_rules_for_unvisited_sibling_paths(self):
        self.nested_directory_alias()
        for unrelated in ("safe/file.txt", "alias/file.txt"):
            for action in ("deny", "approval_required", "generated"):
                generator = ("generator",) if action == "generated" else None
                policy = PolicyConfig(1, (
                    self.policy.rules[0],
                    PathRule("unrelated-exact", (unrelated,), ("modify",), action, generator),
                ))
                with self.subTest(unrelated=unrelated, action=action):
                    result = self.classify("outer/file.txt", policy=policy)[0]
                    self.assertEqual(result.path, "safe/sub/file.txt")
                    self.assertEqual(result.action, "review_required")
                    self.assertEqual(result.rule_ids, ("review",))

    def test_nested_alias_ignores_unvisited_broken_or_external_symlink(self):
        target = self.nested_directory_alias()
        outside = self.root / "outside.txt"
        outside.write_text("fake\n")
        sibling = self.repo / "safe/file.txt"
        policy = PolicyConfig(1, (self.policy.rules[0],))
        for link_target in ("missing.txt", outside):
            sibling.symlink_to(link_target)
            self.assertEqual((self.repo / "outer/file.txt").resolve(strict=True), target)
            with self.subTest(link_target=link_target):
                result = self.classify("outer/file.txt", policy=policy)[0]
                self.assertEqual(result.path, "safe/sub/file.txt")
                self.assertEqual(result.action, "review_required")
            sibling.unlink()

    def test_nested_alias_preserves_exact_protection_on_each_actual_route(self):
        self.nested_directory_alias()
        for accessed in ("outer/file.txt", "alias/sub/file.txt", "safe/sub/file.txt"):
            for action in ("deny", "approval_required", "generated"):
                generator = ("generator",) if action == "generated" else None
                policy = PolicyConfig(1, (
                    self.policy.rules[0],
                    PathRule("accessed-exact", (accessed,), ("modify",), action, generator),
                ))
                with self.subTest(accessed=accessed, action=action):
                    result = self.classify("outer/file.txt", policy=policy)[0]
                    self.assertEqual(result.path, "safe/sub/file.txt")
                    self.assertEqual(result.action, action)
                    self.assertEqual(result.generator, generator)

    def test_directory_symlink_routes_include_the_remaining_file_suffix(self):
        (self.repo / "safe/nested").mkdir(parents=True)
        (self.repo / "safe/nested/secret.txt").write_text("fake\n")
        (self.repo / "protected").mkdir()
        (self.repo / "protected/dir-link").symlink_to("../safe", target_is_directory=True)
        (self.repo / "alias").symlink_to("protected/dir-link", target_is_directory=True)
        (self.repo / "outer").symlink_to("alias", target_is_directory=True)
        access = ("protected/dir-link/nested/secret.txt", "alias/nested/secret.txt",
                  "outer/nested/secret.txt", "./outer//nested/secret.txt",
                  "outer/nested/../nested/secret.txt")
        if (self.repo / "outer/NESTED/SECRET.txt").exists():
            access += ("outer/NESTED/SECRET.txt",)
        for action in ("deny", "approval_required", "generated"):
            generator = ("generator",) if action == "generated" else None
            policy = PolicyConfig(1, (
                self.policy.rules[0],
                PathRule("exact", ("protected/dir-link/nested/secret.txt",),
                         ("modify",), action, generator),
            ))
            for path in access:
                self.assertTrue((self.repo / path).samefile(self.repo / "safe/nested/secret.txt"))
                with self.subTest(action=action, path=path):
                    result = self.classify(path, policy=policy)[0]
                    self.assertEqual(result.path, "safe/nested/secret.txt")
                    self.assertEqual(result.action, action)

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
