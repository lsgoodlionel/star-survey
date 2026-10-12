"""Fail-closed installation, upgrade, and removal of Harness-owned files."""

from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys


MANIFEST_PATH = ".agent-harness-install.json"
LEGACY_MANIFEST_PATH = ".agent-harness-install"
FORMAT_VERSION = 3
FORMAT_1_RUNTIME_FILES = (
    "scripts/agent-harness",
    "tools/agent-harness/agent_harness/__init__.py",
    "tools/agent-harness/agent_harness/cli.py",
    "tools/agent-harness/agent_harness/codex_adapter.py",
    "tools/agent-harness/agent_harness/codex_response.schema.json",
    "tools/agent-harness/agent_harness/config.py",
    "tools/agent-harness/agent_harness/diagnostics.py",
    "tools/agent-harness/agent_harness/distribution.py",
    "tools/agent-harness/agent_harness/doctor.py",
    "tools/agent-harness/agent_harness/gate_runner.py",
    "tools/agent-harness/agent_harness/git_guard.py",
    "tools/agent-harness/agent_harness/run_service.py",
    "tools/agent-harness/agent_harness/state.py",
    "tools/agent-harness/drills/run_drills.py",
    "tools/agent-harness/run_tests.py",
)
FORMAT_2_RUNTIME_FILES = FORMAT_1_RUNTIME_FILES
RUNTIME_FILES = FORMAT_2_RUNTIME_FILES + (
    "tools/agent-harness/agent_harness/generated_verifier.py",
)
MANIFEST_FILE_SETS = {
    1: frozenset(FORMAT_1_RUNTIME_FILES),
    2: frozenset(FORMAT_2_RUNTIME_FILES),
    3: frozenset(RUNTIME_FILES),
}
LEGACY_INSTALLS = {
    "0.1.0": (
        {"path": "scripts/agent-harness", "sha256": "622892b4b85db241ba84aee366356119af7805c247bb2e54d081751177f919ad", "mode": "0755"},
        {"path": "tools/agent-harness/agent_harness/__init__.py", "sha256": "3f4ce49ac24b7bea685abb974abcbc7884ed4b36305d10ad79d8a11be313bc3a", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/cli.py", "sha256": "23a4984c49d3458acc67f94020a98e947c53c48b14fb665a531655a42249614e", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/codex_adapter.py", "sha256": "e34fa147a161ffcac113f62ded256502a9690d321c905a597300523356c8580a", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/codex_response.schema.json", "sha256": "3bfa713c51433c6ac896923af21aa1be84412565e0fb0fbf06847f04e41a8313", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/config.py", "sha256": "e3770f41a10b8d691de629e0345e809039e4d5220e75c00f63f1b9278993d221", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/diagnostics.py", "sha256": "a76dd6b8c3ff431b8571b7d0f78b44760c05c4bfb4be71b9d67de609f3e311be", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/doctor.py", "sha256": "cbc85ad50a6a75cbfc5ec63051626c2d974c830b162a0dfbf5d4ae73c8f5463d", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/gate_runner.py", "sha256": "a40fd94a37a5c019ebb4c5d343d61ef81d29134f59bcc220e5338962e95756d5", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/git_guard.py", "sha256": "a284484e6c3e2812fb0823605f980d5aae75547451cdbded0f415bb3a4dc57a5", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/run_service.py", "sha256": "8dc3bac2b51a957fadbcbd888f56a8def63bcaca38281544c339f42e17cc918f", "mode": "0644"},
        {"path": "tools/agent-harness/agent_harness/state.py", "sha256": "6a9acc1663e2db2212fe01844d6a3b4594ea8703ad78a5db8752fcb4d9483f16", "mode": "0644"},
        {"path": "tools/agent-harness/drills/run_drills.py", "sha256": "abae018a97447890c9be99068adf1d21ea64f1261684e6ab9c22a50539397e58", "mode": "0644"},
        {"path": "tools/agent-harness/run_tests.py", "sha256": "97736bec9c2962ba3c0b6f03b99fe9fde3cfe32d0b32fd2ee9c51370311018eb", "mode": "0644"},
    ),
}
HOST_SEED_FILES = (
    "AGENTS.md",
    "docs/agent/AUTONOMY.md",
    "docs/agent/GATE_MATRIX.yaml",
    "docs/agent/HARNESS_DELIVERY.json",
    "docs/agent/HARNESS_HOST.json",
    "docs/agent/PROGRESS.md",
    "docs/agent/PROJECT_MEMORY.md",
    "docs/agent/PROTECTED_PATHS.yaml",
    "docs/agent/STATE_SCHEMA.json",
    "docs/superpowers/plans/approved-plan.md",
    "docs/superpowers/plans/real-executor-smoke.md",
    "tests/fixtures/real_executor_smoke.txt",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DIRECTORY_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0))


class DistributionError(RuntimeError):
    def __init__(self, message, exit_code=3):
        super().__init__(message)
        self.exit_code = exit_code


class _RootIdentityError(DistributionError):
    pass


@dataclass(frozen=True)
class _RepositoryRoot:
    path: Path
    identity: tuple[int, int]

    def __fspath__(self):
        return os.fspath(self.path)


def _relative(value):
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or value != PurePosixPath(value).as_posix()):
        raise DistributionError("安装清单包含非法路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise DistributionError("安装清单包含非法路径")
    return path


def _absolute_target(value):
    if (not isinstance(value, str) or not value.startswith("/") or "\x00" in value
            or "\\" in value or value.endswith("/") or "//" in value
            or any(part in ("", ".", "..") for part in value.split("/")[1:])):
        raise DistributionError("目标必须使用无符号链接的规范绝对路径", 2)
    path = PurePosixPath(value)
    if not path.is_absolute() or path.as_posix() != value:
        raise DistributionError("目标必须使用无符号链接的规范绝对路径", 2)
    return path


def _open_directory_nofollow(path):
    expected_identity = path.identity if isinstance(path, _RepositoryRoot) else None
    raw_path = path.path if isinstance(path, _RepositoryRoot) else Path(path)
    pure = _absolute_target(raw_path.as_posix())
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in pure.parts[1:]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if (expected_identity is not None
                and _identity(os.fstat(descriptor)) != expected_identity):
            raise _RootIdentityError("目标仓库根目录身份变化")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


def _directory_identity(path):
    descriptor = _open_directory_nofollow(path)
    try:
        return _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


@contextmanager
def _parent_descriptor(root, relative, *, create=False):
    """Open every parent from the repository fd without following symlinks."""
    parts = _relative(relative).parts
    descriptors = []
    try:
        current = _open_directory_nofollow(root)
        descriptors.append(current)
        for part in parts[:-1]:
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current)
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
                except OSError as error:
                    raise DistributionError("无法安全创建目标目录") from error
            except OSError as error:
                raise DistributionError("目标路径包含符号链接或非目录组件") from error
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise DistributionError("目标路径包含非目录组件")
            descriptors.append(child)
            current = child
        yield current, parts[-1]
    except FileNotFoundError:
        raise
    except DistributionError:
        raise
    except OSError as error:
        raise DistributionError("无法安全打开目标路径") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _metadata(root, relative, *, required=True):
    try:
        with _parent_descriptor(root, relative) as (parent, name):
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise DistributionError("套件拥有文件缺失")
        return None
    except OSError as error:
        raise DistributionError("无法安全检查目标文件") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DistributionError("目标文件不是普通文件或为符号链接")
    return metadata


def _read_identity(root, relative):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with _parent_descriptor(root, relative) as (parent, name):
            descriptor = os.open(name, flags, dir_fd=parent)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise DistributionError("套件文件身份无效")
                chunks = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        return (b"".join(chunks), stat.S_IMODE(metadata.st_mode),
                                _identity(metadata))
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
    except FileNotFoundError as error:
        raise DistributionError("套件拥有文件缺失") from error
    except OSError as error:
        raise DistributionError("无法安全读取套件文件") from error


def _read(root, relative):
    return _read_identity(root, relative)[0]


def _file_identity(root, relative):
    return _read_identity(root, relative)[2]


def _write_to_descriptor(parent, name, content, mode):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, mode, dir_fd=parent)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
        return _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _retained_path(relative, private):
    parent = _relative(relative).parent
    return (parent / private).as_posix()


def _write_new(root, relative, content, mode, *, retained=None):
    try:
        with _parent_descriptor(root, relative, create=True) as (parent, name):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise DistributionError("拒绝覆盖已有宿主文件")
            private = _private_name(name, "temporary")
            if retained is not None:
                retained.append(_retained_path(relative, private))
            identity = _write_to_descriptor(parent, private, content, mode)
            _publish_private(parent, private, name, identity)
            return identity
    except OSError as error:
        raise DistributionError("无法安全创建套件文件") from error


def _private_name(name, purpose):
    return ("." + name + ".agent-harness-" + purpose + "-" + str(os.getpid())
            + "-" + secrets.token_hex(8))


# POSIX cannot condition unlink on inode identity. These helpers retain private
# entries after verification so a concurrent host replacement is never deleted.
def _restore_quarantine(parent, name, quarantine, identity):
    try:
        os.link(quarantine, name, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
        restored = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(restored.st_mode) or _identity(restored) != identity:
            return False
        return True
    except OSError:
        return False


def _quarantine(parent, name, expected_identity):
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
    quarantine = _private_name(name, "quarantine")
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode)
                or (expected_identity is not None
                    and _identity(metadata) != expected_identity)):
            raise DistributionError("目标文件身份变化")
        try:
            os.stat(quarantine, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DistributionError("升级 quarantine 文件冲突")
        os.rename(name, quarantine, src_dir_fd=parent, dst_dir_fd=parent)
        moved = os.stat(quarantine, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(moved.st_mode)
                or _identity(moved) != _identity(metadata)):
            moved_identity = _identity(moved)
            _restore_quarantine(parent, name, quarantine, moved_identity)
            raise DistributionError("quarantine 后目标文件身份变化")
        return quarantine, _identity(metadata)
    finally:
        os.close(descriptor)


def _unlink_quarantine(parent, quarantine, expected_identity):
    descriptor = os.open(
        quarantine, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent,
    )
    try:
        metadata = os.fstat(descriptor)
        current = os.stat(quarantine, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(metadata.st_mode) or not stat.S_ISREG(current.st_mode)
                or _identity(metadata) != expected_identity
                or _identity(current) != expected_identity):
            raise DistributionError("quarantine 文件身份变化")
    finally:
        os.close(descriptor)


def _publish_private(parent, private, name, expected_identity):
    os.link(private, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
    published = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(published.st_mode) or _identity(published) != expected_identity:
        raise DistributionError("发布时目标文件身份变化")


def _replace(root, relative, content, mode, *, expected_identity=None, retained=None):
    name = _relative(relative).name
    temporary = _private_name(name, "temporary")
    try:
        with _parent_descriptor(root, relative) as (parent, target_name):
            quarantine = None
            quarantine_identity = None
            published = False
            try:
                if retained is not None:
                    retained.append(_retained_path(relative, temporary))
                new_identity = _write_to_descriptor(parent, temporary, content, mode)
                quarantine, quarantine_identity = _quarantine(
                    parent, target_name, expected_identity,
                )
                if retained is not None:
                    retained.append(_retained_path(relative, quarantine))
                _publish_private(parent, temporary, target_name, new_identity)
                published = True
                _unlink_quarantine(parent, quarantine, quarantine_identity)
                quarantine = None
                return new_identity
            except Exception:
                if quarantine is not None:
                    if published:
                        try:
                            published_quarantine, published_identity = _quarantine(
                                parent, target_name, new_identity,
                            )
                        except Exception:
                            pass
                        else:
                            if _restore_quarantine(
                                    parent, target_name, quarantine, quarantine_identity):
                                try:
                                    _unlink_quarantine(
                                        parent, published_quarantine, published_identity,
                                    )
                                except Exception:
                                    pass
                                quarantine = None
                    else:
                        if _restore_quarantine(
                                parent, target_name, quarantine, quarantine_identity):
                            quarantine = None
                raise
    except FileNotFoundError as error:
        raise DistributionError("套件拥有文件缺失") from error
    except OSError as error:
        raise DistributionError("无法原子替换套件文件") from error


def _unlink(root, relative, *, expected_identity=None, retained=None):
    try:
        with _parent_descriptor(root, relative) as (parent, name):
            quarantine = None
            quarantine_identity = None
            try:
                quarantine, quarantine_identity = _quarantine(parent, name, expected_identity)
                if retained is not None:
                    retained.append(_retained_path(relative, quarantine))
                _unlink_quarantine(parent, quarantine, quarantine_identity)
                quarantine = None
            except Exception:
                if quarantine is not None:
                    _restore_quarantine(parent, name, quarantine, quarantine_identity)
                raise
    except FileNotFoundError as error:
        raise DistributionError("套件拥有文件缺失") from error
    except OSError as error:
        raise DistributionError("无法安全删除套件文件") from error


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _entry(relative, content, mode):
    return {"path": relative, "sha256": _sha256(content), "mode": f"{mode:04o}"}


def _source_inventory(source):
    entries = []
    for relative in RUNTIME_FILES:
        content = _read(source, relative)
        mode = 0o755 if relative == "scripts/agent-harness" else 0o644
        entries.append((_entry(relative, content, mode), content))
    return entries


def _validate_repository(target):
    try:
        raw = _absolute_target(str(target))
        root = Path(raw.as_posix())
        original_identity = _directory_identity(root)
    except (DistributionError, OSError, RuntimeError, ValueError) as error:
        if isinstance(error, DistributionError):
            raise
        raise DistributionError("目标必须是可访问的 Git 仓库", 2) from error
    git = "/usr/bin/git"
    environment = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
    commands = (("rev-parse", "--show-toplevel"),
                ("rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"))
    outputs = []
    for command in commands:
        try:
            result = subprocess.run([git, "--no-optional-locks", "--no-replace-objects", *command],
                                    cwd=root, text=True, capture_output=True, check=False,
                                    timeout=5, env=environment, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DistributionError("无法验证目标 Git 仓库", 2) from error
        if result.returncode:
            raise DistributionError("目标必须是已有提交的 Git 仓库", 2)
        outputs.append(result.stdout.strip())
    try:
        current_identity = _directory_identity(root)
        if (Path(outputs[0]) != root or current_identity != original_identity
                or not re.fullmatch(r"[0-9a-f]{40,64}", outputs[1])):
            raise DistributionError("目标必须是仓库根目录且 HEAD 明确", 2)
    except (DistributionError, OSError, RuntimeError, ValueError) as error:
        if isinstance(error, DistributionError):
            raise
        raise DistributionError("目标仓库身份无效", 2) from error
    return _RepositoryRoot(root, original_identity)


def _manifest_bytes(version, owned, seeded):
    paths = frozenset(entry["path"] for entry in owned)
    if paths != MANIFEST_FILE_SETS.get(FORMAT_VERSION):
        raise DistributionError("当前版本的套件文件集合未登记")
    document = {
        "formatVersion": FORMAT_VERSION,
        "suiteVersion": version,
        "ownedFiles": sorted(owned, key=lambda entry: entry["path"]),
        "seededFiles": sorted(seeded, key=lambda entry: entry["path"]),
    }
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _load_manifest(root):
    try:
        json_metadata = _metadata(root, MANIFEST_PATH, required=False)
        legacy_metadata = _metadata(root, LEGACY_MANIFEST_PATH, required=False)
        if json_metadata is not None and legacy_metadata is not None:
            raise ValueError()
        if json_metadata is None:
            if legacy_metadata is None:
                raise ValueError()
            content, _, identity = _read_identity(root, LEGACY_MANIFEST_PATH)
            version = content.removesuffix(b"\n").decode("utf-8")
            if content != (version + "\n").encode("utf-8") or version not in LEGACY_INSTALLS:
                raise ValueError()
            document = {
                "formatVersion": 0,
                "suiteVersion": version,
                "ownedFiles": [dict(entry) for entry in LEGACY_INSTALLS[version]],
                "seededFiles": [],
            }
            return document, LEGACY_MANIFEST_PATH, identity
        content, _, identity = _read_identity(root, MANIFEST_PATH)
        document = json.loads(content)
        if (set(document) != {"formatVersion", "suiteVersion", "ownedFiles", "seededFiles"}
                or document["formatVersion"] not in MANIFEST_FILE_SETS
                or not isinstance(document["suiteVersion"], str)
                or not isinstance(document["ownedFiles"], list)
                or not isinstance(document["seededFiles"], list)):
            raise ValueError()
        seen = set()
        for entry in document["ownedFiles"]:
            if (set(entry) != {"path", "sha256", "mode"}
                    or _SHA256.fullmatch(entry["sha256"]) is None
                    or re.fullmatch(r"0[0-7]{3}", entry["mode"]) is None):
                raise ValueError()
            entry["path"] = _relative(entry["path"]).as_posix()
            if entry["path"] in seen:
                raise ValueError()
            seen.add(entry["path"])
        if seen != MANIFEST_FILE_SETS[document["formatVersion"]]:
            raise ValueError()
        for entry in document["seededFiles"]:
            if (set(entry) != {"path", "sha256"} or _SHA256.fullmatch(entry["sha256"]) is None):
                raise ValueError()
            _relative(entry["path"])
        return document, MANIFEST_PATH, identity
    except (DistributionError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, DistributionError):
            raise
        raise DistributionError("安装清单无效") from error


def _verify_owned(root, entries):
    identities = {}
    for entry in entries:
        content, mode, identity = _read_identity(root, entry["path"])
        if _sha256(content) != entry["sha256"] or mode != int(entry["mode"], 8):
            raise DistributionError("套件拥有文件已被修改，拒绝继续")
        identities[entry["path"]] = identity
    return identities


def _backup_suite(root, manifest, manifest_path, backup, retained=None):
    snapshot = [dict(entry) for entry in manifest["ownedFiles"]]
    manifest_content, manifest_mode, _ = _read_identity(root, manifest_path)
    snapshot.append(_entry(manifest_path, manifest_content, manifest_mode))
    for entry in snapshot:
        _write_new(root, backup + "/" + entry["path"], _read(root, entry["path"]),
                   int(entry["mode"], 8), retained=retained)
    _verify_owned(root, [
        {**entry, "path": backup + "/" + entry["path"]}
        for entry in snapshot
    ])
    return snapshot


def _restore_suite(root, backup, snapshot, created_paths, transaction_identities,
                   retained=None):
    restored = True
    for relative in sorted(created_paths):
        try:
            current = _metadata(root, relative, required=False)
            expected = transaction_identities.get(relative)
            if current is not None:
                if expected is None or _identity(current) != expected:
                    restored = False
                    continue
                _unlink(root, relative, expected_identity=expected, retained=retained)
        except Exception:
            restored = False
    for entry in snapshot:
        try:
            content = _read(root, backup + "/" + entry["path"])
            mode = int(entry["mode"], 8)
            current = _metadata(root, entry["path"], required=False)
            expected = transaction_identities.get(entry["path"])
            if current is None:
                if expected is not None:
                    restored = False
                    continue
                _write_new(root, entry["path"], content, mode, retained=retained)
            else:
                if expected is None or _identity(current) != expected:
                    restored = False
                    continue
                _replace(root, entry["path"], content, mode, expected_identity=expected,
                         retained=retained)
        except Exception:
            restored = False
    try:
        _verify_owned(root, snapshot)
        if any(_metadata(root, relative, required=False) is not None for relative in created_paths):
            restored = False
    except Exception:
        restored = False
    return restored


def _prune(root, paths):
    directories = sorted({_relative(path).parent for path in paths},
                         key=lambda path: len(path.parts), reverse=True)
    for directory in directories:
        while directory != PurePosixPath("."):
            try:
                with _parent_descriptor(root, directory.as_posix()) as (parent, name):
                    metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                        break
                    os.rmdir(name, dir_fd=parent)
            except _RootIdentityError:
                raise
            except (DistributionError, FileNotFoundError, OSError):
                break
            directory = directory.parent


def _retained_note(paths):
    unique = list(dict.fromkeys(paths))
    if not unique:
        return ""
    shown = "、".join(unique[:3])
    remainder = f" 等 {len(unique)} 项" if len(unique) > 3 else ""
    return f"；保留隔离文件：{shown}{remainder}"


def install(source, target):
    source = Path(source).resolve(strict=True)
    root = _validate_repository(target)
    if (_metadata(root, MANIFEST_PATH, required=False) is not None
            or _metadata(root, LEGACY_MANIFEST_PATH, required=False) is not None):
        raise DistributionError("Harness 已安装；请使用 upgrade.sh")
    inventory = _source_inventory(source)
    for entry, _ in inventory:
        if _metadata(root, entry["path"], required=False) is not None:
            raise DistributionError("拒绝覆盖已有宿主文件")
    seeded = []
    seed_content = []
    for relative in HOST_SEED_FILES:
        source_relative = "templates/host/" + relative
        content = _read(source, source_relative)
        if _metadata(root, relative, required=False) is None:
            seed_content.append((relative, content))
            seeded.append({"path": relative, "sha256": _sha256(content)})
    created = []
    retained = []
    try:
        for entry, content in inventory:
            identity = _write_new(root, entry["path"], content, int(entry["mode"], 8),
                                  retained=retained)
            created.append((entry["path"], identity))
        for relative, content in seed_content:
            identity = _write_new(root, relative, content, 0o644, retained=retained)
            created.append((relative, identity))
        version = _read(source, "VERSION").decode().strip()
        _write_new(root, MANIFEST_PATH,
                   _manifest_bytes(version, [entry for entry, _ in inventory], seeded), 0o600,
                   retained=retained)
    except Exception as error:
        for relative, identity in reversed(created):
            try:
                _unlink(root, relative, expected_identity=identity, retained=retained)
            except DistributionError:
                pass
        _prune(root, [relative for relative, _ in created])
        if isinstance(error, DistributionError):
            raise DistributionError(str(error) + _retained_note(retained),
                                    error.exit_code) from None
        raise
    print(f"Installed control plane {version}; host templates were seeded without overwrites"
          + _retained_note(retained) + ".")


def upgrade(source, target):
    source = Path(source).resolve(strict=True)
    root = _validate_repository(target)
    manifest, manifest_path, manifest_identity = _load_manifest(root)
    identities = _verify_owned(root, manifest["ownedFiles"])
    identities[manifest_path] = manifest_identity
    inventory = _source_inventory(source)
    old = {entry["path"]: entry for entry in manifest["ownedFiles"]}
    new = {entry["path"]: (entry, content) for entry, content in inventory}
    for relative in new.keys() - old.keys():
        if _metadata(root, relative, required=False) is not None:
            raise DistributionError("升级拒绝覆盖非套件文件")
    backup = (".agent-harness-backups/"
              + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
              + "-" + str(os.getpid()))
    retained = []
    snapshot = _backup_suite(root, manifest, manifest_path, backup, retained=retained)
    created_paths = set(new.keys() - old.keys())
    if manifest_path != MANIFEST_PATH:
        created_paths.add(MANIFEST_PATH)
    transaction_identities = dict(identities)
    try:
        for relative, (entry, content) in new.items():
            if relative in old:
                transaction_identities[relative] = _replace(
                    root, relative, content, int(entry["mode"], 8),
                    expected_identity=transaction_identities[relative],
                    retained=retained,
                )
            else:
                transaction_identities[relative] = _write_new(
                    root, relative, content, int(entry["mode"], 8), retained=retained,
                )
        for relative in old.keys() - new.keys():
            _unlink(root, relative, expected_identity=transaction_identities[relative],
                    retained=retained)
            transaction_identities.pop(relative)
        version = _read(source, "VERSION").decode().strip()
        manifest_content = _manifest_bytes(
            version, [entry for entry, _ in inventory], manifest["seededFiles"],
        )
        if manifest_path == MANIFEST_PATH:
            transaction_identities[MANIFEST_PATH] = _replace(
                root, MANIFEST_PATH, manifest_content, 0o600,
                expected_identity=transaction_identities[MANIFEST_PATH],
                retained=retained,
            )
        else:
            transaction_identities[MANIFEST_PATH] = _write_new(
                root, MANIFEST_PATH, manifest_content, 0o600, retained=retained,
            )
            _unlink(root, manifest_path, expected_identity=transaction_identities[manifest_path],
                    retained=retained)
            transaction_identities.pop(manifest_path)
    except Exception:
        restored = _restore_suite(
            root, backup, snapshot, created_paths, transaction_identities, retained=retained,
        )
        result = "成功" if restored else "失败"
        raise DistributionError(
            f"升级失败；备份位置：{backup}；恢复结果：{result}" + _retained_note(retained)
        ) from None
    _prune(root, old.keys() - new.keys())
    print(f"Upgraded control plane to {version}; backup: {root.path / backup}"
          + _retained_note(retained))


def uninstall(target):
    root = _validate_repository(target)
    manifest, manifest_path, manifest_identity = _load_manifest(root)
    identities = _verify_owned(root, manifest["ownedFiles"])
    paths = [entry["path"] for entry in manifest["ownedFiles"]]
    retained = []
    for relative in paths:
        _unlink(root, relative, expected_identity=identities[relative], retained=retained)
    _unlink(root, manifest_path, expected_identity=manifest_identity, retained=retained)
    _prune(root, paths)
    print("Removed hash-matching Harness-owned paths; host policy, seeds, backups and runtime history were preserved"
          + _retained_note(retained) + ".")


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(arguments) == 3 and arguments[0] in ("install", "upgrade"):
            globals()[arguments[0]](arguments[1], arguments[2])
        elif len(arguments) == 2 and arguments[0] == "uninstall":
            uninstall(arguments[1])
        else:
            raise DistributionError("无效的 distribution manager 参数", 2)
        return 0
    except DistributionError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
