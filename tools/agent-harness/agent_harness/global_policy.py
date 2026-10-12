"""Install reusable token-efficiency policy into marker-owned Codex files."""

from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import tempfile


class GlobalPolicyError(ValueError):
    """The global policy target or a managed marker block is unsafe."""


@dataclass(frozen=True)
class GlobalPolicyResult:
    changed: tuple[str, ...]
    diff: str
    dry_run: bool

    def to_dict(self):
        return {"changed": list(self.changed), "diff": self.diff,
                "dryRun": self.dry_run}


_TARGETS = (
    (Path("AGENTS.md"), "codex-token-efficiency"),
    (Path("memories/engineering-token-efficiency.md"),
     "codex-engineering-token-efficiency"),
    (Path("memories/project-context-index.md"), "codex-project-context-index"),
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as error:
        raise GlobalPolicyError("Global policy target is unreadable") from error


def _safe_target(home: Path, relative: Path) -> Path:
    current = home
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise GlobalPolicyError("Global policy target traverses a symbolic link")
    target = home / relative
    if not target.resolve(strict=False).is_relative_to(home):
        raise GlobalPolicyError("Global policy target escapes Codex home")
    return target


def _managed(content: str, identifier: str, body: str) -> str:
    start = f"<!-- {identifier}:start -->"
    end = f"<!-- {identifier}:end -->"
    if any(marker in body for marker in (start, end)):
        raise GlobalPolicyError("Global policy template contains managed markers")
    starts, ends = content.count(start), content.count(end)
    if starts != ends or starts > 1:
        raise GlobalPolicyError("Global policy marker pair is malformed")
    block = f"{start}\n{body.rstrip()}\n{end}"
    if starts == 0:
        separator = "" if not content or content.endswith("\n") else "\n"
        return content + separator + block + "\n"
    begin = content.index(start)
    try:
        finish = content.index(end, begin) + len(end)
    except ValueError as error:
        raise GlobalPolicyError("Global policy marker order is invalid") from error
    suffix = content[finish:]
    return content[:begin] + block + suffix


def _atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _templates(repo: Path) -> dict[Path, str]:
    base = repo / "tools/agent-harness/templates"
    rules = _read(base / "global-token-rules.md")
    method = _read(base / "engineering-token-efficiency.md")
    if not rules.strip() or not method.strip():
        raise GlobalPolicyError("Global policy templates are unavailable")
    try:
        host = json.loads((repo / "docs/agent/HARNESS_HOST.json")
                          .read_text(encoding="utf-8"))
        projections = json.loads((repo / "docs/agent/MEMORY_PROJECTIONS.json")
                                 .read_text(encoding="utf-8"))["projections"]
        repository = host["expectedRepository"]
        notes = [row["notePath"] for row in projections]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise GlobalPolicyError("Project context configuration is unavailable") from error
    if (not isinstance(repository, str) or not repository
            or not isinstance(notes, list)
            or any(not isinstance(note, str) or Path(note).is_absolute()
                   or ".." in Path(note).parts for note in notes)):
        raise GlobalPolicyError("Project context configuration is unsafe")
    note_lines = "\n".join(f"  - `{note}`" for note in notes)
    index = (
        "# Project context index\n\n"
        f"- Repository: `{repository}`\n"
        "- Authority: repository `AGENTS.md`, approved spec/plan, Harness state, and Git.\n"
        "- Startup: consume the generated Context Capsule and task delta; do not load full plans, logs, or vault notes by default.\n"
        "- Obsidian projection entries (relative to the locally configured vault):\n"
        f"{note_lines}\n"
        "- Dynamic progress belongs in Harness state and verified projections, not this index.\n"
    )
    return {_TARGETS[0][0]: rules, _TARGETS[1][0]: method,
            _TARGETS[2][0]: index}


def install_global_policy(repo_root: Path, codex_home: Path, *,
                          dry_run: bool = False) -> GlobalPolicyResult:
    repo = Path(repo_root).resolve(strict=True)
    home_input = Path(codex_home).expanduser()
    if (not home_input.is_absolute() or not home_input.is_dir()
            or home_input.is_symlink()):
        raise GlobalPolicyError("Codex home must be an existing non-symlink directory")
    home = home_input.resolve(strict=True)
    templates = _templates(repo)
    changes = []
    diffs = []
    writes = []
    for relative, identifier in _TARGETS:
        target = _safe_target(home, relative)
        before = _read(target)
        after = _managed(before, identifier, templates[relative])
        if before == after:
            continue
        changes.append(relative.as_posix())
        diffs.extend(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=relative.as_posix(), tofile=relative.as_posix(),
        ))
        writes.append((target, after))
    if not dry_run:
        for target, content in writes:
            _atomic(target, content)
    return GlobalPolicyResult(tuple(changes), "".join(diffs), dry_run)
