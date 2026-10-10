"""Read-only Git boundaries and conservative, canonical path decisions.

An explainable descendant has exactly one Agent-Run-Id trailer per commit,
matching RunState.run_id, and touches only RunState.changed_paths. Dirty state
cannot be authenticated by the current state schema and always pauses resume.
"""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Iterable, Optional, Tuple

from .config import PolicyConfig
from .state import RunState


class GitGuardError(ValueError):
    """Git identity, query or isolation cannot be established safely."""


@dataclass(frozen=True)
class GitSnapshot:
    repo: Path
    branch: str
    head_commit: str
    dirty_paths: Tuple[str, ...]
    untracked_paths: Tuple[str, ...]


@dataclass(frozen=True)
class PathDecision:
    path: str
    action: str
    rule_ids: Tuple[str, ...]
    reason: str
    generator: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class ResumeDecision:
    allowed: bool
    reason: str
    invalidate_gates: bool = False


def _git(repo, *args):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "--no-replace-objects",
             "-c", "core.fsmonitor=false", *args],
            cwd=repo, text=True, encoding="utf-8", errors="surrogateescape",
            capture_output=True, check=False,
        )
    except (OSError, ValueError) as error:
        raise GitGuardError("Cannot execute Git query") from error
    return result


def _query(repo, *args):
    result = _git(repo, *args)
    if result.returncode != 0:
        # Git stderr may contain credentials or attacker-controlled paths.
        raise GitGuardError("Git query failed: " + args[0])
    return result.stdout


def _repo_root(repo):
    try:
        root = Path(repo).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise GitGuardError("Cannot resolve repository") from error
    top = _query(root, "rev-parse", "--show-toplevel").rstrip("\n")
    if Path(top).resolve() != root:
        raise GitGuardError("Expected repository root, not a nested checkout directory")
    return root


def _commit(repo, revision):
    if not isinstance(revision, str) or not revision or "\x00" in revision:
        raise GitGuardError("Invalid commit revision")
    oid = _query(repo, "rev-parse", "--verify", "--end-of-options",
                 revision + "^{commit}").strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None:
        raise GitGuardError("Ambiguous commit revision")
    return oid


def capture_snapshot(repo: Path) -> GitSnapshot:
    root = _repo_root(repo)
    output = _query(root, "status", "--porcelain=v2", "--branch", "-z",
                    "--untracked-files=all", "--ignore-submodules=none")
    records = iter(output.split("\x00"))
    head, branch = None, None
    dirty, untracked = set(), set()
    for record in records:
        if not record:
            continue
        if record.startswith("# branch.oid "):
            head = record[len("# branch.oid "):]
        elif record.startswith("# branch.head "):
            branch = record[len("# branch.head "):]
        elif record.startswith("# "):
            continue
        elif record.startswith("? "):
            path = record[2:]
            dirty.add(path)
            untracked.add(path)
        elif record[0] in ("1", "2", "u"):
            count = {"1": 8, "2": 9, "u": 10}[record[0]]
            fields = record.split(" ", count)
            if len(fields) != count + 1 or not fields[-1]:
                raise GitGuardError("Malformed porcelain-v2 status")
            dirty.add(fields[-1])
            if record[0] == "2":
                original = next(records, "")
                if not original:
                    raise GitGuardError("Missing porcelain-v2 rename source")
                dirty.add(original)
        else:
            raise GitGuardError("Unknown porcelain-v2 status record")
    if branch == "(detached)":
        raise GitGuardError("Cannot operate on detached HEAD")
    if head == "(initial)":
        raise GitGuardError("Cannot operate on unborn HEAD")
    if not branch or head is None or _commit(root, "HEAD") != head:
        raise GitGuardError("Ambiguous or changing HEAD")
    return GitSnapshot(root, branch, head, tuple(sorted(dirty)), tuple(sorted(untracked)))


def assert_worktree_isolated(repo: Path) -> None:
    snapshot = capture_snapshot(repo)
    git_dir = Path(_query(snapshot.repo, "rev-parse", "--absolute-git-dir").rstrip("\n"))
    common = Path(_query(snapshot.repo, "rev-parse", "--git-common-dir").rstrip("\n"))
    if not common.is_absolute():
        common = snapshot.repo / common
    if git_dir.resolve() == common.resolve():
        raise GitGuardError("Autonomous execution refuses a shared checkout")
    # A .git file alone is insufficient: submodules and separate-git-dir clones
    # are not linked worktrees. Verify the reciprocal worktree registration.
    marker = snapshot.repo / ".git"
    registration = git_dir / "gitdir"
    try:
        registered = Path(registration.read_text(encoding="utf-8").rstrip("\n"))
        if not registered.is_absolute():
            registered = git_dir / registered
        valid = marker.is_file() and registered.resolve(strict=True) == marker.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        valid = False
    if not valid:
        raise GitGuardError("Cannot establish linked worktree isolation")


def _diff_paths(repo, base, head):
    output = _query(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                    "--name-only", "-z", base, head, "--")
    return tuple(path for path in output.split("\x00") if path)


def changed_paths(repo: Path, base: str, head: Optional[str] = None) -> Tuple[str, ...]:
    root = _repo_root(repo)
    base_oid = _commit(root, base)
    snapshot = capture_snapshot(root) if head is None else None
    head_oid = snapshot.head_commit if snapshot else _commit(root, head)
    paths = set(_diff_paths(root, base_oid, head_oid))
    if snapshot:
        paths.update(snapshot.dirty_paths)
    return tuple(sorted(paths))


def _inside(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_path(root, value, links=()):
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive):
        raise ValueError("Invalid repository-relative path")
    current = root
    lexical = []
    routes = set()
    # Keep dot components until after the directory check: file/. is invalid.
    for part in (part for part in value.split("/") if part):
        if current.exists() and not current.is_dir():
            raise ValueError("Non-directory path ancestor")
        if part == ".":
            continue
        if part == "..":
            if not lexical:
                raise ValueError("Parent escape")
            lexical.pop()
            current = current.parent
        else:
            lexical.append(part)
            candidate = current / part
            # Preserve exact names on case-sensitive filesystems. On a
            # case-insensitive volume use the actual directory entry spelling.
            if current.is_dir():
                entries = tuple(current.iterdir())
                exact = next((entry for entry in entries if entry.name == part), None)
                if exact is not None:
                    candidate = exact
                elif candidate.exists() or candidate.is_symlink():
                    matches = [entry for entry in entries if entry.samefile(candidate)]
                    if len(matches) != 1:
                        raise ValueError("Ambiguous filesystem case")
                    candidate = matches[0]
            lexical[-1] = candidate.name
            if candidate.is_symlink():
                if candidate in links or len(links) >= 40:
                    raise ValueError("Symlink cycle or excessive depth")
                target = candidate.readlink()
                if not target.is_absolute():
                    target = candidate.parent / target
                if not _inside(target, root):
                    raise ValueError("Symlink escape")
                resolved, spelling, target_routes = _canonical_path(
                    root, target.relative_to(root).as_posix(), links + (candidate,))
                routes.update((candidate.relative_to(root).as_posix(), resolved, spelling))
                routes.update(target_routes)
                current = root / resolved
                if not current.exists():
                    raise ValueError("Unresolved symlink target")
            else:
                current = candidate
        if not _inside(current, root):
            raise ValueError("Symlink or parent escape")
    if current == root or not lexical:
        raise ValueError("Expected a file path")
    canonical = current.relative_to(root).as_posix()
    original = "/".join(lexical)
    if canonical.split("/")[0] == ".git" or original.split("/")[0] == ".git":
        raise ValueError("Git metadata is protected")
    return canonical, original, tuple(sorted(routes))


def classify_paths(repo: Path, paths: Iterable[str], policy: PolicyConfig) -> Tuple[PathDecision, ...]:
    root = _repo_root(repo)
    precedence = {"deny": 0, "approval_required": 1, "generated": 2, "review_required": 3}
    decisions = []
    for value in paths:
        try:
            canonical, lexical, routes = _canonical_path(root, value)
        except (OSError, RuntimeError, ValueError):
            decisions.append(PathDecision(value, "deny", (), "path_boundary"))
            continue
        # This interface has no operation parameter, so consider every rule
        # that covers any operation. Never infer deletion from file existence.
        matches = [rule for rule in policy.rules if any(
            fnmatchcase(path, PurePosixPath(pattern).as_posix())
            for path in (canonical, lexical, *routes) for pattern in rule.patterns
        )]
        if not matches:
            decisions.append(PathDecision(canonical, "review_required", (), "no_matching_rule"))
            continue
        action = min((rule.action for rule in matches), key=precedence.__getitem__)
        winners = [rule for rule in matches if rule.action == action]
        generators = {rule.generator for rule in winners} if action == "generated" else set()
        if len(generators) > 1:
            decisions.append(PathDecision(canonical, "deny", tuple(rule.id for rule in winners),
                                          "conflicting_generators"))
        else:
            decisions.append(PathDecision(canonical, action, tuple(rule.id for rule in winners),
                                          "policy_rule", next(iter(generators), None)))
    return tuple(decisions)


def _ancestor(repo, older, newer):
    result = _git(repo, "merge-base", "--is-ancestor", older, newer)
    if result.returncode not in (0, 1):
        raise GitGuardError("Ambiguous ancestry")
    return result.returncode == 0


def _owned_descendants(repo, old, new, state):
    permitted = set()
    for path in state.changed_paths:
        canonical, lexical, _ = _canonical_path(repo, path.as_posix())
        permitted.update((canonical, lexical))
    commits = _query(repo, "rev-list", "--reverse", "--parents", old + ".." + new).splitlines()
    previous = old
    for entry in commits:
        fields = entry.split()
        if len(fields) != 2 or fields[1] != previous:
            return False
        commit = fields[0]
        trailers = _query(repo, "show", "-s", "--format=%(trailers:key=Agent-Run-Id,valueonly,unfold)",
                          commit, "--").splitlines()
        if [value for value in trailers if value] != [state.run_id]:
            return False
        touched = _diff_paths(repo, previous, commit)
        if not set(touched) <= permitted:
            return False
        previous = commit
    return bool(commits) and previous == new


def validate_resume(snapshot: GitSnapshot, state: RunState) -> ResumeDecision:
    try:
        recorded_root = state.worktree_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return ResumeDecision(False, "worktree_drift")
    if snapshot.repo != recorded_root:
        return ResumeDecision(False, "worktree_drift")
    assert_worktree_isolated(snapshot.repo)
    if capture_snapshot(snapshot.repo) != snapshot:
        return ResumeDecision(False, "snapshot_drift")
    if snapshot.branch != state.branch:
        return ResumeDecision(False, "branch_drift")
    if snapshot.dirty_paths or snapshot.untracked_paths:
        return ResumeDecision(False, "dirty_drift")
    try:
        if _query(snapshot.repo, "rev-parse", "--is-shallow-repository").strip() != "false":
            return ResumeDecision(False, "ambiguous_ancestry")
        old = _commit(snapshot.repo, state.head_commit)
        base = _commit(snapshot.repo, state.base_commit)
        if not _ancestor(snapshot.repo, base, old):
            return ResumeDecision(False, "ambiguous_ancestry")
        if old == snapshot.head_commit:
            decision = ResumeDecision(True, "exact_match")
        else:
            if not _ancestor(snapshot.repo, old, snapshot.head_commit):
                return ResumeDecision(False, "history_drift")
            if not _owned_descendants(snapshot.repo, old, snapshot.head_commit, state):
                return ResumeDecision(False, "unexplained_head")
            decision = ResumeDecision(True, "explainable_descendant", True)
    except (GitGuardError, OSError, RuntimeError, ValueError):
        return ResumeDecision(False, "ambiguous_ancestry")
    if capture_snapshot(snapshot.repo) != snapshot:
        return ResumeDecision(False, "snapshot_drift")
    return decision
