"""Fixed least-privilege argv and bounded, untrusted Codex JSONL transport."""

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PureWindowsPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Sequence
from uuid import UUID

from .diagnostics import redact_text, _open_directory
from .git_guard import assert_worktree_isolated
from .state import _read_json

_SCHEMA = _read_json(Path(__file__).with_name("codex_response.schema.json").read_bytes())
_LINE_LIMIT = 65536
_TOTAL_LIMIT = 4 * 1024 * 1024
_EVENT_LIMIT = 4096
_PROMPT_LIMIT = 16384
_FORBIDDEN = re.compile(r"bypass|danger-full|--add-dir|--config|(?:^|\s)-c(?:\s|=|$)|--enable|--disable|--profile", re.I)


@dataclass(frozen=True)
class _BoundTool:
    name: str
    entry: str | None
    target: str | None
    directory: str | None
    root: str | None
    trusted_roots: tuple[str, ...] | None
    entry_identity: tuple[int, int, int, int, int, int] | None
    target_identity: tuple[int, int, int, int, int, int] | None
    chain_identities: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...] | None


def _identity(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_mode,
            metadata.st_size, metadata.st_ctime_ns)


def _expected_entry_name(name, path):
    if name == "python":
        return re.fullmatch(r"python(?:3(?:\.1[1-4])?)?", path.name) is not None
    return path.name == name


def _expected_target_name(name, target):
    return (_expected_entry_name(name, target)
            or (name == "npm" and target.name == "npm-cli.js"))


def _codex_candidates():
    if sys.platform == "darwin":
        return (("/Applications/ChatGPT.app/Contents/Resources/codex-cli/"
                 "CodexCLI.app/Contents/MacOS/codex", "/Applications/ChatGPT.app"),)
    if sys.platform.startswith("linux"):
        system_roots = ("/usr", "/etc/alternatives") if Path("/etc/alternatives").is_dir() else "/usr"
        return (("/usr/bin/codex", system_roots), ("/usr/local/bin/codex", "/usr/local"))
    return ()


def _account_home():
    if os.name != "posix":
        raise ValueError("No fixed account home policy for this platform")
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)


def _candidate_roots(name):
    candidates = []
    if sys.platform == "darwin":
        fixed = {
            "git": ("/usr/bin/git", "/usr"),
            "java": ("/usr/bin/java", "/usr"),
            "docker": ("/Applications/Docker.app/Contents/Resources/bin/docker", "/Applications/Docker.app"),
        }
        if name in fixed:
            candidates.append(fixed[name])
        if name == "docker":
            candidates.extend((
                ("/usr/local/bin/docker", ("/usr/local", "/Applications/Docker.app")),
                ("/opt/homebrew/bin/docker", ("/opt/homebrew", "/Applications/Docker.app")),
            ))
    elif sys.platform.startswith("linux"):
        for prefix in ("/usr", "/usr/local", "/opt/homebrew"):
            roots = ((prefix, "/etc/alternatives")
                     if prefix == "/usr" and Path("/etc/alternatives").is_dir() else prefix)
            candidates.append((prefix + "/bin/" + name, roots))
    else:
        return ()
    if name not in ("git", "java", "docker") or sys.platform != "darwin":
        for prefix in ("/opt/homebrew", "/usr/local"):
            candidates.append((prefix + "/bin/" + name, prefix))
    return tuple(candidates)


def _versioned_candidates(name):
    try:
        home = _account_home()
        if name == "python":
            root = home / ".local/share/uv/python"
            values = []
            if (3, 11) <= sys.version_info[:2] <= (3, 14):
                executable = Path(sys.executable).resolve(strict=True)
                if executable.is_relative_to(root.resolve(strict=True)):
                    values.append(executable)
            for minor in range(11, 15):
                values.extend(root.glob(f"cpython-3.{minor}.*-*/bin/python3.{minor}"))
            values = sorted(set(values), reverse=True)
            return tuple((str(value), str(root)) for value in values)
        if name == "node":
            root = home / ".nvm/versions/node"
            values = sorted(root.glob("v22.*/bin/node"), reverse=True)
            return tuple((str(value), str(value.parents[1])) for value in values)
    except (KeyError, OSError, RuntimeError):
        return ()
    return ()


def _matching_root(path, roots):
    matches = [root for root in roots if path.is_relative_to(root)]
    if not matches:
        raise ValueError("Tool path escaped its trusted roots")
    return max(matches, key=lambda value: len(value.parts))


def _symlink_chain(path, roots):
    chain, current, seen = [], path, set()
    for _ in range(17):
        if current in seen:
            raise ValueError("Tool symlink escaped its trusted root")
        _matching_root(current, roots)
        seen.add(current)
        metadata = current.lstat()
        chain.append((str(current), _identity(metadata)))
        if not stat.S_ISLNK(metadata.st_mode):
            return tuple(chain), current
        target = Path(os.readlink(current))
        current = Path(os.path.abspath(target if target.is_absolute() else current.parent / target))
    raise ValueError("Tool symlink chain is too deep")


def _trusted_directories(path, root, owner):
    current = path
    while True:
        metadata = current.stat()
        if metadata.st_uid not in (0, owner) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("Project tool parent is not trusted")
        if current == root:
            return
        if current == current.parent or not current.is_relative_to(root):
            raise ValueError("Project tool escaped its fixed root")
        current = current.parent


def _bound_tool(name, candidates, *, target_directory=False, allow_link=None):
    empty = _BoundTool(name, None, None, None, None, None, None, None, None)
    for value, allowed in candidates:
        try:
            path = Path(value)
            allowed_values = allowed if isinstance(allowed, (tuple, list)) else (allowed,)
            roots = tuple(Path(item).resolve(strict=True) for item in allowed_values)
            root = _matching_root(path, roots)
            root_owner = root.stat().st_uid
            allowed_owners = (0, root_owner)
            if (not path.is_absolute() or not _expected_entry_name(name, path)
                    or redact_text(str(path)) != str(path)):
                continue
            entry_metadata = path.lstat()
            link_allowed = (allow_link is True or (allow_link is None and sys.platform.startswith("linux")))
            if stat.S_ISLNK(entry_metadata.st_mode) and not link_allowed:
                continue
            chain, chain_target = _symlink_chain(path, roots)
            target = path.resolve(strict=True)
            target_root = _matching_root(target, roots)
            if not _expected_target_name(name, target):
                continue
            target_metadata = target.stat()
            target_owner = target_root.stat().st_uid
            if (not stat.S_ISREG(target_metadata.st_mode) or not os.access(path, os.X_OK)
                    or target_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                    or entry_metadata.st_uid not in allowed_owners
                    or target_metadata.st_uid not in (0, target_owner)
                    or entry_metadata.st_uid not in (0, os.getuid())
                    or target_metadata.st_uid not in (0, os.getuid())):
                continue
            if any(identity[2] not in (0, _matching_root(Path(item), roots).stat().st_uid)
                   or (not stat.S_ISLNK(identity[3]) and identity[3] & (stat.S_IWGRP | stat.S_IWOTH))
                   for item, identity in chain):
                continue
            if chain_target.resolve(strict=True) != target:
                continue
            directory = target.parent if target_directory else path.parent.resolve(strict=True)
            for item, _ in chain:
                item_path = Path(item)
                item_root = _matching_root(item_path, roots)
                _trusted_directories(item_path.parent, item_root, item_root.stat().st_uid)
            _trusted_directories(target.parent, target_root, target_owner)
            return _BoundTool(name, str(path), str(target), str(directory), str(root),
                              tuple(str(item) for item in roots),
                              _identity(entry_metadata), _identity(target_metadata), chain)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return empty


def _bind_requested_tool(name, requested):
    if not isinstance(requested, str) or not Path(requested).is_absolute():
        raise ValueError("Requested tool must be an absolute path")
    candidates = tuple(candidate for candidate in _candidate_roots(name)
                       if candidate[0] == requested)
    bound = _bound_tool(name, candidates, allow_link=name == "docker")
    if bound.entry != requested:
        raise ValueError("Requested tool is not trusted")
    return bound


def _npm_candidates(node):
    try:
        if not node.entry or not node.root:
            return ()
        return ((str(Path(node.entry).with_name("npm")), node.root),)
    except (OSError, RuntimeError, TypeError):
        return ()


def _discover_tools():
    python_system = tuple((prefix + "/bin/python3." + str(minor), prefix)
                          for prefix in ("/usr", "/usr/local", "/opt/homebrew")
                          for minor in range(11, 15))
    python = _bound_tool("python", _versioned_candidates("python") + python_system,
                         target_directory=True)
    node = _bound_tool("node", _versioned_candidates("node") + _candidate_roots("node"))
    npm = _bound_tool("npm", _npm_candidates(node), allow_link=True)
    return (python, node, npm,
            _bound_tool("docker", _candidate_roots("docker"), allow_link=True),
            _bound_tool("git", _candidate_roots("git")),
            _bound_tool("java", _candidate_roots("java")))


_BOUND_TOOLS = _discover_tools()
_BOUND_CODEX_TOOL = _bound_tool("codex", _codex_candidates())
_BOUND_CODEX = _BOUND_CODEX_TOOL.entry


class CodexFailure(str, Enum):
    COMMAND_POLICY = "command_policy"
    ENVIRONMENT_POLICY = "environment_policy"
    STORAGE_POLICY = "storage_policy"
    SPAWN_ERROR = "spawn_error"
    CLEANUP_ERROR = "cleanup_error"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    MALFORMED_JSONL = "malformed_jsonl"
    MISSING_SESSION = "missing_session"
    MISSING_RESPONSE = "missing_response"
    MISSING_COMPLETION = "missing_completion"
    SCHEMA_VIOLATION = "schema_violation"
    TURN_FAILED = "turn_failed"


@dataclass(frozen=True)
class CodexResult:
    status: str = "failed"
    summary: str = "Codex execution failed"
    changed_paths: tuple[str, ...] = ()
    tests_requested: tuple[str, ...] = ()
    needs_human: bool = True
    session_id: str | None = None
    failure: CodexFailure | None = None
    exit_code: int | None = None


def _session(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid session identity")
    return value


def _safe(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit
            or "\x00" in value or _FORBIDDEN.search(value) or redact_text(value) != value):
        raise ValueError("Unsafe command input")
    return value


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected canonical absolute path")
    descriptor = _open_directory(path if path.is_dir() else path.parent)
    os.close(descriptor)
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ValueError("Symlink path rejected")
    _safe(str(path), 4096)
    return path


def _trusted_codex(repo, candidate=None):
    value = candidate if candidate is not None else _BOUND_CODEX
    if not value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("Codex executable is not an absolute trusted path")
    entry = Path(value)
    path = entry.resolve(strict=True)
    if (_BOUND_CODEX is None or entry != Path(_BOUND_CODEX)
            or path != Path(_BOUND_CODEX_TOOL.target)):
        raise ValueError("Codex executable does not match the bound identity")
    root = Path(repo).resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    if path.is_relative_to(root) or path.is_relative_to(temporary):
        raise ValueError("Codex executable is inside an autonomous writable root")
    _trusted_tool_directory(repo, _BOUND_CODEX_TOOL)
    return entry


def build_codex_command(repo: Path, schema: Path, prompt: str,
                        resume_session: str | None = None) -> tuple[str, ...]:
    root, schema = _path(repo), _path(schema)
    assert_worktree_isolated(root)
    if not schema.is_relative_to(root) or schema.stat().st_size > _LINE_LIMIT or _read_json(schema.read_bytes()) != _SCHEMA:
        raise ValueError("Expected the controlled response schema")
    prompt = _safe(prompt, _PROMPT_LIMIT)
    if prompt.lstrip().startswith("-"):
        raise ValueError("Prompt must not be parsed as an option")
    executable = _trusted_codex(root)
    fixed = (str(executable), "exec", "--sandbox", "workspace-write", "--approve-for-me",
             "--strict-config", "--json", "--output-schema", str(schema), "--cd", str(root))
    # Parent exec options precede resume: resume does not expose sandbox/cd.
    return fixed + (("resume", _session(resume_session)) if resume_session is not None else ()) + (prompt,)


def _command(command):
    if not isinstance(command, (tuple, list)) or len(command) not in (12, 14):
        raise ValueError("Command rejected")
    command = tuple(command)
    session = command[12] if len(command) == 14 else None
    root = Path(command[10])
    executable = _trusted_codex(root, command[0])
    if Path(command[0]) != executable:
        raise ValueError("Executable identity changed")
    root, schema = _path(root), _path(command[8])
    assert_worktree_isolated(root)
    if not schema.is_relative_to(root) or schema.stat().st_size > _LINE_LIMIT or _read_json(schema.read_bytes()) != _SCHEMA:
        raise ValueError("Expected the controlled response schema")
    prompt = _safe(command[-1], _PROMPT_LIMIT)
    if prompt.lstrip().startswith("-"):
        raise ValueError("Prompt must not be parsed as an option")
    expected = (str(executable), "exec", "--sandbox", "workspace-write", "--approve-for-me",
                "--strict-config", "--json", "--output-schema", str(schema), "--cd", str(root))
    expected += (("resume", _session(session)) if session is not None else ()) + (prompt,)
    if command != expected:
        raise ValueError("Command rejected")
    return command, Path(command[10])


def _storage(root, event_log):
    path = Path(event_log)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Raw log path rejected")
    relative = path.relative_to(root)
    if len(relative.parts) < 5 or relative.parts[:3] != ("var", "agent-harness", "runs"):
        raise ValueError("Raw log must belong to a run")
    _session(relative.parts[3])
    parent = _open_directory(path.parent)
    try:
        arguments = [Path(*relative.parts[:4]).as_posix() + "/",
                     relative.as_posix(), relative.as_posix() + ".stderr.log"]
        result = subprocess.run(["git", "--no-optional-locks", "check-ignore", "--no-index", "-z", "--stdin"],
                                cwd=root, input="\x00".join(arguments) + "\x00", text=True, capture_output=True, check=False)
        tracked = subprocess.run(["git", "--no-optional-locks", "ls-files", "-z", "--", *arguments],
                                 cwd=root, capture_output=True, check=False)
        if result.returncode or set(result.stdout.rstrip("\x00").split("\x00")) != set(arguments) or tracked.returncode or tracked.stdout:
            raise ValueError("Raw outputs must be ignored and untracked")
        for name in (path.name, path.name + ".stderr.log"):
            if os.path.lexists(path.parent / name):
                raise ValueError("Raw output already exists")
        return path, parent
    except BaseException:
        os.close(parent)
        raise


def _validate_response(value, schema):
    kinds = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "boolean": type(value) is bool}
    if not kinds.get(schema["type"], False):
        raise ValueError("Response type mismatch")
    if isinstance(value, dict):
        if set(value) != set(schema["required"]):
            raise ValueError("Response field mismatch")
        for key, item in value.items():
            _validate_response(item, schema["properties"][key])
    elif isinstance(value, list):
        if len(value) > schema["maxItems"] or any(item in value[:i] for i, item in enumerate(value)):
            raise ValueError("Response array limit")
        for item in value:
            _validate_response(item, schema["items"])
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", _LINE_LIMIT):
            raise ValueError("Response string limit")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Response enum mismatch")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError("Response pattern mismatch")
        if schema.get("format") == "uuid":
            _session(value)


def _response(document, session):
    _validate_response(document, _SCHEMA)
    for key in ("changedPaths", "testsRequested"):
        if any(redact_text(v) != v for v in document[key]):
            raise ValueError("Unsafe response metadata")
    for value in document["changedPaths"]:
        path = Path(value)
        if (path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or "\\" in value
                or "\x00" in value or path.as_posix() != value or value == "."):
            raise ValueError("Invalid changed path")
    if _session(document["sessionId"]) != session:
        raise ValueError("Session mismatch")
    return CodexResult(document["status"], redact_text(document["summary"]),
                       tuple(document["changedPaths"]), tuple(document["testsRequested"]),
                       document["needsHuman"], session, exit_code=0)


def _environment_path(value, *, multiple=False):
    if not isinstance(value, str) or not value or redact_text(value) != value or "\x00" in value:
        raise ValueError("Unsafe environment path")
    values = value.split(os.pathsep) if multiple else (value,)
    if any(not item or not Path(item).is_absolute() or ".." in Path(item).parts for item in values):
        raise ValueError("Environment paths must be absolute")
    resolved = []
    for item in values:
        try:
            resolved.append(Path(item).resolve(strict=True))
        except (OSError, RuntimeError):
            if not multiple:
                raise
    resolved = tuple(resolved)
    if not resolved:
        raise ValueError("Environment path has no usable entries")
    if any(not item.is_dir() for item in resolved):
        raise ValueError("Environment path is not a directory")
    return os.pathsep.join(str(item) for item in resolved)


def _trusted_tool_directory(repo, tool):
    if not all((tool.entry, tool.target, tool.directory, tool.root, tool.trusted_roots,
                tool.entry_identity, tool.target_identity)):
        raise ValueError("Required project tool is unavailable")
    entry = Path(tool.entry)
    root_policy = Path(tool.root)
    trusted_roots = tuple(Path(value) for value in tool.trusted_roots)
    root_owner = root_policy.stat().st_uid
    entry_metadata = entry.lstat()
    target = entry.resolve(strict=True)
    directory = entry.parent.resolve(strict=True)
    expected_directory = Path(tool.directory)
    if tool.name == "python":
        directory = target.parent
    root = Path(repo).resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    target_root = _matching_root(target, trusted_roots)
    target_owner = target_root.stat().st_uid
    values = (entry, target, directory, *trusted_roots)
    if (target != Path(tool.target) or directory != expected_directory
            or not _expected_entry_name(tool.name, entry)
            or not _expected_target_name(tool.name, target)
            or _identity(entry_metadata) != tool.entry_identity
            or _identity(target.stat()) != tool.target_identity
            or _symlink_chain(entry, trusted_roots)[0] != tool.chain_identities
            or _matching_root(entry, trusted_roots) != root_policy
            or not any(target.is_relative_to(value) for value in trusted_roots)
            or any(redact_text(str(value)) != str(value) for value in values)
            or any(value.is_relative_to(root) or value.is_relative_to(temporary) for value in values)):
        raise ValueError("Project tool identity is not trusted")
    metadata = target.stat()
    if (not stat.S_ISREG(metadata.st_mode) or not os.access(entry, os.X_OK)
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or entry_metadata.st_uid not in (0, root_owner) or metadata.st_uid not in (0, target_owner)
            or entry_metadata.st_uid not in (0, os.getuid()) or metadata.st_uid not in (0, os.getuid())):
        raise ValueError("Project tool is not executable")
    for item, _ in tool.chain_identities:
        item_path = Path(item)
        item_root = _matching_root(item_path, trusted_roots)
        _trusted_directories(item_path.parent, item_root, item_root.stat().st_uid)
    _trusted_directories(target.parent, target_root, target_owner)
    return directory


def controlled_tool_errors(repo, *, required_tools=None, require_codex=True):
    errors = []
    if require_codex:
        try:
            _trusted_codex(repo)
        except (OSError, RuntimeError, TypeError, ValueError):
            errors.append({"id": "controlled-tool:codex",
                           "message": "受控工具不可用或不受信任"})
    names = {tool.name for tool in _BOUND_TOOLS} if required_tools is None else set(required_tools)
    for tool in (item for item in _BOUND_TOOLS if item.name in names):
        try:
            _trusted_tool_directory(repo, tool)
        except (OSError, RuntimeError, TypeError, ValueError):
            errors.append({"id": "controlled-tool:" + tool.name,
                           "message": "受控工具不可用或不受信任"})
    return tuple(errors)


def controlled_tool_path(repo, name):
    tool = next((item for item in _BOUND_TOOLS if item.name == name), None)
    if tool is None or not tool.entry:
        raise ValueError("Required project tool is unavailable")
    _trusted_tool_directory(repo, tool)
    return Path(tool.entry)


def run_with_controlled_tools(repo, operation, *, required_tools=None, require_codex=True):
    errors = controlled_tool_errors(repo, required_tools=required_tools,
                                    require_codex=require_codex)
    if errors:
        raise ValueError("Controlled tools are unavailable")
    environment = _environment(repo, required_tools=required_tools,
                               require_codex=require_codex)
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(environment)
        return operation(repo)
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _environment(repo, *, required_tools=None, require_codex=True, optional_tools=()):
    environment = {}
    if "PATH" in os.environ and (redact_text(os.environ["PATH"]) != os.environ["PATH"] or "\x00" in os.environ["PATH"]):
        raise ValueError("Unsafe inherited PATH")
    directories = [_trusted_codex(repo).parent] if require_codex else []
    names = {tool.name for tool in _BOUND_TOOLS} if required_tools is None else set(required_tools)
    directories.extend(_trusted_tool_directory(repo, tool)
                       for tool in _BOUND_TOOLS if tool.name in names)
    for tool in _BOUND_TOOLS:
        if tool.name not in optional_tools or tool.name in names:
            continue
        try:
            directories.append(_trusted_tool_directory(repo, tool))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    system_bin = Path("/bin").resolve(strict=True)
    if (system_bin.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or system_bin.is_relative_to(Path(repo).resolve(strict=True))):
        raise ValueError("System tool directory is not trusted")
    directories.append(system_bin)
    environment["PATH"] = os.pathsep.join(dict.fromkeys(str(path) for path in directories))
    for name in ("HOME", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            environment[name] = _environment_path(os.environ[name])
    for name in ("LANG", "LC_ALL"):
        if name in os.environ:
            value = os.environ[name]
            if redact_text(value) != value or re.fullmatch(r"[A-Za-z0-9_.@-]+", value) is None:
                raise ValueError("Unsafe locale environment")
            environment[name] = value
    if "TZ" in os.environ:
        value = os.environ["TZ"]
        if (redact_text(value) != value or re.fullmatch(r"[A-Za-z0-9_+./-]+", value) is None
                or ".." in Path(value).parts):
            raise ValueError("Unsafe timezone environment")
        environment["TZ"] = value
    return environment


def _wait_process(process, timeout=None):
    return process.wait(timeout=timeout)


def _kill_process(process):
    process.kill()


def _close_resource(resource):
    resource.close()


def run_codex(command: Sequence[str], timeout_seconds: int, event_log: Path) -> CodexResult:
    """Never return arbitrary exception/log text; kill and reap owned processes."""
    try:
        root_hint = Path(command[10])
        environment = _environment(root_hint, required_tools=("git",), require_codex=True)
    except (IndexError, OSError, RuntimeError, TypeError, ValueError):
        return CodexResult(failure=CodexFailure.ENVIRONMENT_POLICY)
    try:
        command, root = _command(command)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ValueError()
    except (OSError, ValueError, TypeError, IndexError):
        return CodexResult(failure=CodexFailure.COMMAND_POLICY)
    try:
        path, parent = _storage(root, event_log)
    except (OSError, ValueError, TypeError):
        return CodexResult(failure=CodexFailure.STORAGE_POLICY)
    process = None
    failure, exit_code = None, None
    session, response, completed = None, None, False
    total, count, buffer = 0, 0, b""
    deadline = time.monotonic() + timeout_seconds
    try:
        with os.fdopen(os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=parent), "wb") as stdout, \
                os.fdopen(os.open(path.name + ".stderr.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=parent), "wb") as stderr, selectors.DefaultSelector() as selector:
            process = subprocess.Popen(command, cwd=root, env=environment, shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=(os.name == "posix"))
            for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, target)
            while selector.get_map() and failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = CodexFailure.TIMEOUT
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    allowed = min(len(chunk), _TOTAL_LIMIT - total)
                    key.data.write(chunk[:allowed])
                    total += len(chunk)
                    if total > _TOTAL_LIMIT:
                        failure = CodexFailure.OUTPUT_LIMIT
                        break
                    if key.fileobj is process.stderr:
                        continue
                    buffer += chunk
                    while b"\n" in buffer and failure is None:
                        line, buffer = buffer.split(b"\n", 1)
                        count += 1
                        if len(line) > _LINE_LIMIT or count > _EVENT_LIMIT:
                            failure = CodexFailure.OUTPUT_LIMIT
                            break
                        try:
                            event = _read_json(line)
                            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                                raise ValueError()
                            kind = event["type"]
                            if kind == "thread.started":
                                identity = _session(event.get("thread_id"))
                                if session is not None or completed:
                                    raise ValueError()
                                session = identity
                            elif kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                                if completed:
                                    raise ValueError()
                                # Only the last agent message is the final response.
                                response = event["item"]["text"]
                            elif kind == "turn.completed":
                                if completed:
                                    raise ValueError()
                                completed = True
                            elif kind in ("turn.failed", "error"):
                                failure = CodexFailure.TURN_FAILED
                        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                            failure = CodexFailure.MALFORMED_JSONL
                    if len(buffer) > _LINE_LIMIT:
                        failure = CodexFailure.OUTPUT_LIMIT
            if failure is None:
                try:
                    exit_code = _wait_process(process, timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    failure = CodexFailure.TIMEOUT
            if failure is None and exit_code != 0:
                failure = CodexFailure.NONZERO_EXIT
            if failure is None and buffer:
                failure = CodexFailure.MALFORMED_JSONL
    except OSError:
        failure = CodexFailure.CLEANUP_ERROR if process is not None else CodexFailure.SPAWN_ERROR
    finally:
        if process is not None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.poll() is None:
                    _kill_process(process)
            except ProcessLookupError:
                pass
            except PermissionError:
                failure = failure or CodexFailure.CLEANUP_ERROR
                try:
                    if process.poll() is None:
                        _kill_process(process)
                except (OSError, ValueError):
                    failure = CodexFailure.CLEANUP_ERROR
            except OSError:
                failure = CodexFailure.CLEANUP_ERROR
                try:
                    if process.poll() is None:
                        _kill_process(process)
                except (OSError, ValueError):
                    failure = CodexFailure.CLEANUP_ERROR
                    try:
                        if process.poll() is None:
                            process.kill()
                    except (OSError, ValueError):
                        failure = CodexFailure.CLEANUP_ERROR
            try:
                _wait_process(process)
            except (OSError, ValueError):
                failure = CodexFailure.CLEANUP_ERROR
                try:
                    process.wait()
                except (OSError, ValueError):
                    failure = CodexFailure.CLEANUP_ERROR
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        _close_resource(stream)
                    except (OSError, ValueError):
                        failure = CodexFailure.CLEANUP_ERROR
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            failure = CodexFailure.CLEANUP_ERROR
        try:
            os.close(parent)
        except OSError:
            failure = CodexFailure.CLEANUP_ERROR
    if failure is None:
        failure = (CodexFailure.MISSING_SESSION if session is None else
                   CodexFailure.MISSING_RESPONSE if response is None else
                   CodexFailure.MISSING_COMPLETION if not completed else None)
    if failure is None:
        try:
            if len(command) == 14 and session != command[12]:
                raise ValueError("Resumed session mismatch")
            return _response(_read_json(response), session)
        except (ValueError, TypeError, KeyError, RecursionError):
            failure = CodexFailure.SCHEMA_VIOLATION
    return CodexResult(failure=failure, exit_code=exit_code)
