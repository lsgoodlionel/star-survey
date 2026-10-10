"""Read-only, bounded discovery of the local Harness environment."""

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

from .config import load_gate_matrix, load_protected_paths
from .git_guard import assert_worktree_isolated, capture_snapshot


@dataclass(frozen=True)
class DoctorReport:
    ok: tuple
    warning: tuple
    error: tuple

    def to_dict(self):
        return {name: list(getattr(self, name)) for name in ("ok", "warning", "error")}


def run_doctor(repo: Path) -> DoctorReport:
    checks = {"ok": [], "warning": [], "error": []}

    def add(level, identifier, message):
        checks[level].append({"id": identifier, "message": message})

    root = Path(repo).resolve()
    for name, required, major in (("git", True, None), ("docker", False, None),
                                  ("node", False, 22), ("java", False, 21),
                                  ("codex", False, None)):
        executable = shutil.which(name)
        level = "error" if required else "warning"
        if executable is None:
            add(level, name, "未找到命令")
            continue
        try:
            result = subprocess.run([executable, "-version" if name == "java" else "--version"],
                                    cwd=root, text=True, capture_output=True, check=False,
                                    timeout=5, stdin=subprocess.DEVNULL)
            output = result.stdout + result.stderr
            version = re.search(r'(?:version\s+"?|^v)(\d+)', output)
            if result.returncode or (major is not None and (version is None or int(version[1]) != major)):
                add(level, name, "命令不可用或版本不符" + (f"；需要 {major}" if major else ""))
            else:
                add("ok", name, "命令可用" + (f"；版本 {major}" if major else ""))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            add(level, name, "命令检查失败或超时")
    add("ok" if sys.version_info >= (3, 11) else "error", "python",
        "Python " + ".".join(map(str, sys.version_info[:3])) + "；需要 3.11+")
    try:
        snapshot = capture_snapshot(root)
        add("ok", "repository", "Git 仓库与分支有效")
        assert_worktree_isolated(root)
        add("ok", "worktree", "独立 worktree")
        add("warning" if snapshot.dirty_paths else "ok", "workspace",
            "工作区存在改动" if snapshot.dirty_paths else "工作区干净")
    except ValueError:
        add("error", "worktree", "无法验证仓库、分支或独立 worktree")
    try:
        usage = shutil.disk_usage(root)
        add("ok" if usage.free >= 1024**3 else "warning", "disk",
            "磁盘空间充足" if usage.free >= 1024**3 else "可用空间少于 1 GiB")
    except OSError:
        add("error", "disk", "无法检查磁盘空间")
    required = ("AGENTS.md", "docs/agent/STATE_SCHEMA.json", "docs/agent/PROJECT_MEMORY.md",
                "docs/agent/AUTONOMY.md", "docs/agent/GATE_MATRIX.yaml", "docs/agent/PROTECTED_PATHS.yaml")
    for relative in required:
        path = root / relative
        valid = path.is_file() and not any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root))
        add("ok" if valid else "error", "file:" + relative, "文件存在" if valid else "必需文件缺失或为符号链接")
    try:
        matrix = load_gate_matrix(root / "docs/agent/GATE_MATRIX.yaml")
        load_protected_paths(root / "docs/agent/PROTECTED_PATHS.yaml")
        add("ok", "config", "策略配置有效")
        for gate in matrix.gates:
            wrapper = len(gate.command) > 1 and gate.command[1] == "--python"
            if not wrapper and not Path(gate.command[0]).name.startswith("python"):
                continue
            command = ([str((root / gate.cwd / gate.command[0]).resolve()), "--python", "--version"]
                       if wrapper else [gate.command[0], "--version"])
            try:
                result = subprocess.run(command, cwd=root / gate.cwd, text=True,
                                        capture_output=True, check=False, timeout=5,
                                        stdin=subprocess.DEVNULL)
                version = re.fullmatch(r"Python (\d+)\.(\d+)\.\d+\s*", result.stdout)
                valid = result.returncode == 0 and version is not None and tuple(map(int, version.groups())) >= (3, 11)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                valid = False
            add("ok" if valid else "error", "gate-python:" + gate.id,
                "Gate Python 3.11+ 可用" if valid else "Gate Python 缺失或低于 3.11")
    except ValueError:
        add("error", "config", "策略配置无效")
    for port in (3000, 8080):
        try:
            with socket.socket() as probe:
                probe.settimeout(0.05)
                occupied = probe.connect_ex(("127.0.0.1", port)) == 0
            add("warning" if occupied else "ok", f"port:{port}",
                "本机端口已有服务" if occupied else "本机端口未发现服务")
        except OSError:
            add("warning", f"port:{port}", "无法检查本机端口")
    return DoctorReport(*(tuple(checks[name]) for name in ("ok", "warning", "error")))
