"""Re-run registered generators in a disposable workspace before authorization."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import signal
import stat
import subprocess
import tempfile
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Tuple

from .git_guard import PathDecision


DEFAULT_GENERATOR_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class GeneratedVerification:
    passed: bool
    failure_kind: Optional[str]
    metadata_path: Path
    binding: Mapping[str, object]


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(part in ("", ".", "..") for part in PurePosixPath(value).parts)):
        raise ValueError("Expected a canonical repository-relative path")
    return PurePosixPath(value).as_posix()


def _environment(isolation_root: Path) -> dict[str, str]:
    names = ("PATH", "LANG", "LC_ALL", "TZ", "JAVA_HOME", "SYSTEMROOT", "WINDIR")
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.setdefault("PATH", os.defpath)
    home = isolation_root / "home"
    temporary = isolation_root / "tmp"
    home.mkdir()
    temporary.mkdir()
    environment["HOME"] = str(home)
    for name in ("TMPDIR", "TMP", "TEMP"):
        environment[name] = str(temporary)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_inventory(repo: Path) -> Tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "--no-replace-objects", "-c",
             "core.fsmonitor=false", "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"],
            cwd=repo, capture_output=True, check=False,
        )
    except OSError as error:
        raise ValueError("Cannot inventory generator inputs") from error
    if result.returncode:
        raise ValueError("Cannot inventory generator inputs")
    values = []
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        value = os.fsdecode(raw)
        _relative(value)
        if value.startswith("var/agent-harness/") or value == "var/agent-harness":
            continue
        values.append(value)
    return tuple(sorted(set(values), key=lambda item: os.fsencode(item)))


def _fingerprint(path: Path) -> Tuple[str, str, int]:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISREG(metadata.st_mode):
        return "file", _sha256(path), mode
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        return "symlink", hashlib.sha256(os.fsencode(target)).hexdigest(), mode
    if stat.S_ISDIR(metadata.st_mode):
        return "directory", "", mode
    raise ValueError("Generator input contains an unsupported file type")


def _manifest(repo: Path, outputs: frozenset[str]) -> Tuple[Tuple[str, Tuple[str, str, int]], ...]:
    records = []
    for relative in _git_inventory(repo):
        if relative in outputs:
            continue
        path = repo / relative
        if not path.exists() and not path.is_symlink():
            continue
        records.append((relative, _fingerprint(path)))
    return tuple(records)


def _manifest_digest(records: Iterable[Tuple[str, Tuple[str, str, int]]]) -> str:
    digest = hashlib.sha256()
    for relative, (kind, content, mode) in records:
        for value in (relative, kind, content, str(mode)):
            encoded = value.encode("utf-8", errors="surrogateescape")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _contained_symlink(repo: Path, path: Path) -> None:
    target = Path(os.readlink(path))
    candidate = target if target.is_absolute() else path.parent / target
    try:
        candidate.resolve(strict=True).relative_to(repo)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("Generator input symlink escapes repository") from error


def _copy_inputs(repo: Path, workspace: Path,
                 records: Iterable[Tuple[str, Tuple[str, str, int]]]) -> None:
    for relative, (kind, _, _) in records:
        source = repo / relative
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            shutil.copy2(source, target, follow_symlinks=False)
        elif kind == "symlink":
            _contained_symlink(repo, source)
            target.symlink_to(os.readlink(source), target_is_directory=source.resolve().is_dir())
        elif kind == "directory":
            target.mkdir(exist_ok=True)


def _tree_snapshot(root: Path, workspace: Path) -> Mapping[str, Tuple[str, str, int]]:
    records = {}
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in (*names, *files):
            path = parent / name
            relative = path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) \
                else "../" + path.relative_to(root).as_posix()
            records[relative] = _fingerprint(path)
    return records


def _prepare_command(workspace: Path, command: Tuple[str, ...], environment: Mapping[str, str]) -> Optional[str]:
    executable = command[0]
    if "/" in executable:
        raw_executable = Path(executable)
        if raw_executable.is_absolute():
            if not raw_executable.is_file():
                return "missing_executable"
        else:
            candidate = workspace / _relative(executable.removeprefix("./"))
            if not candidate.is_file() or candidate.is_symlink():
                return "missing_generator"
    elif shutil.which(executable, path=environment["PATH"]) is None:
        return "missing_executable"
    if (Path(executable).name.startswith("python") and len(command) > 1
            and not command[1].startswith("-") and "/" in command[1]):
        try:
            script = workspace / _relative(command[1])
        except ValueError:
            return "missing_generator"
        if not script.is_file() or script.is_symlink():
            return "missing_generator"
    return None


def _stop_process(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def _evidence_directory(repo: Path, evidence_dir: Path) -> Path:
    root = repo.resolve(strict=True)
    raw = evidence_dir if evidence_dir.is_absolute() else root / evidence_dir
    try:
        relative = raw.relative_to(root)
    except ValueError as error:
        raise ValueError("Generated evidence escapes repository") from error
    relative_text = _relative(relative.as_posix())
    if not Path(relative_text).is_relative_to(Path("var/agent-harness/runs")):
        raise ValueError("Generated evidence must be stored in the raw run directory")
    current = root
    for part in PurePosixPath(relative_text).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Generated evidence directory cannot contain symlinks")
    current.mkdir(parents=True, exist_ok=True)
    return current


def _write_metadata(path: Path, metadata: Mapping[str, object]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".metadata.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(metadata, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _verify_one(repo: Path, command: Tuple[str, ...], outputs: Tuple[str, ...],
                evidence_dir: Path, timeout_seconds: int) -> GeneratedVerification:
    output_set = frozenset(outputs)
    attempt = Path(tempfile.mkdtemp(prefix="generator-", dir=evidence_dir))
    metadata_path = attempt / "metadata.json"
    stdout_path, stderr_path = attempt / "stdout.log", attempt / "stderr.log"
    started = datetime.now(timezone.utc)
    failure, exit_code = None, None
    unexpected = []
    expected_hashes = {}
    try:
        records = _manifest(repo, output_set)
        input_digest = _manifest_digest(records)
        for relative in outputs:
            path = repo / relative
            if not path.is_file() or path.is_symlink():
                failure = "output_missing"
                break
            expected_hashes[relative] = _sha256(path)
        with tempfile.TemporaryDirectory(prefix="agent-harness-generator-") as temporary:
            isolation_root = Path(temporary)
            workspace = isolation_root / "workspace"
            workspace.mkdir()
            _copy_inputs(repo, workspace, records)
            for relative in outputs:
                (workspace / relative).parent.mkdir(parents=True, exist_ok=True)
            environment = _environment(isolation_root)
            before = _tree_snapshot(isolation_root, workspace)
            if failure is None:
                failure = _prepare_command(workspace, command, environment)
            process = None
            with stdout_path.open("x", encoding="utf-8") as stdout, \
                    stderr_path.open("x", encoding="utf-8") as stderr:
                if failure is None:
                    try:
                        process = subprocess.Popen(
                            command, cwd=workspace, env=environment, shell=False,
                            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                            text=True, start_new_session=(os.name == "posix"),
                        )
                        try:
                            exit_code = process.wait(timeout=timeout_seconds)
                            if exit_code != 0:
                                failure = "nonzero_exit"
                        except subprocess.TimeoutExpired:
                            failure = "timeout"
                    except FileNotFoundError:
                        failure = "missing_executable"
                    except OSError:
                        failure = "spawn_error"
                    finally:
                        if process is not None:
                            _stop_process(process)
            after = _tree_snapshot(isolation_root, workspace)
            changed = sorted(path for path in set(before) | set(after)
                             if before.get(path) != after.get(path))
            unexpected = [
                path for path in changed
                if path not in output_set
                and not path.startswith("../home/")
                and not path.startswith("../tmp/")
            ]
            if unexpected:
                failure = "out_of_scope_write"
            if failure is None and _manifest_digest(_manifest(repo, output_set)) != input_digest:
                failure = "input_mismatch"
            if failure is None:
                for relative in outputs:
                    generated = workspace / relative
                    if not generated.is_file() or generated.is_symlink():
                        failure = "output_missing"
                        break
                    if generated.read_bytes() != (repo / relative).read_bytes():
                        failure = "output_mismatch"
                        break
    except (OSError, RuntimeError, ValueError):
        failure = failure or "input_mismatch"
        input_digest = ""
        if not stdout_path.exists():
            stdout_path.touch()
        if not stderr_path.exists():
            stderr_path.touch()
    ended = datetime.now(timezone.utc)
    passed = failure is None
    binding = {
        "version": 1,
        "status": "passed" if passed else "failed",
        "failureKind": failure,
        "command": list(command),
        "outputs": expected_hashes,
        "inputSha256": input_digest,
        "metadataPath": metadata_path.relative_to(repo).as_posix(),
    }
    metadata = {
        **binding,
        "binding": binding,
        "outputs": list(outputs),
        "outputSha256": expected_hashes,
        "startedAt": _timestamp(started),
        "endedAt": _timestamp(ended),
        "timeoutSeconds": timeout_seconds,
        "exitCode": exit_code,
        "stdoutPath": "stdout.log",
        "stderrPath": "stderr.log",
        "unexpectedPaths": unexpected,
    }
    _write_metadata(metadata_path, metadata)
    return GeneratedVerification(passed, failure, metadata_path.relative_to(repo),
                                 MappingProxyType(binding))


def verify_generated_outputs(repo: Path, decisions: Iterable[PathDecision],
                             evidence_dir: Path, *,
                             timeout_seconds: int = DEFAULT_GENERATOR_TIMEOUT_SECONDS,
                             ) -> Tuple[GeneratedVerification, ...]:
    """Regenerate each command's exact outputs and return immutable evidence bindings."""
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("Generator timeout must be a positive integer")
    root = Path(repo).resolve(strict=True)
    directory = _evidence_directory(root, Path(evidence_dir))
    groups = {}
    for decision in decisions:
        if decision.action != "generated":
            continue
        command = decision.generator
        if (not isinstance(command, tuple) or not command
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)):
            raise ValueError("Generated path requires a fixed argv")
        groups.setdefault(command, set()).add(_relative(decision.path))
    return tuple(_verify_one(root, command, tuple(sorted(outputs)), directory, timeout_seconds)
                 for command, outputs in sorted(groups.items()))


def generated_binding_is_current(repo: Path, binding: Mapping[str, object]) -> bool:
    """Check that a successful binding still describes current input and output bytes."""
    try:
        if (set(binding) != {"version", "status", "failureKind", "command", "outputs",
                             "inputSha256", "metadataPath"}
                or binding["version"] != 1 or binding["status"] != "passed"
                or binding["failureKind"] is not None
                or not isinstance(binding["command"], list) or not binding["command"]
                or not isinstance(binding["outputs"], dict) or not binding["outputs"]):
            return False
        root = Path(repo).resolve(strict=True)
        outputs = {_relative(path): digest for path, digest in binding["outputs"].items()}
        if any(not isinstance(digest, str) or len(digest) != 64 for digest in outputs.values()):
            return False
        current = {path: _sha256(root / path) for path in outputs
                   if (root / path).is_file() and not (root / path).is_symlink()}
        if current != outputs:
            return False
        return _manifest_digest(_manifest(root, frozenset(outputs))) == binding["inputSha256"]
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
