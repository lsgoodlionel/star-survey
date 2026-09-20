"""日志来源采集。

只取尾部：故障现场几乎总在末尾，全量日志既超限又没人看。
任何一个来源取不到都只记一行说明，绝不让整次上报失败——半份证据也比没有强。
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

_READ_TAIL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str  # file | docker
    location: str  # 文件路径或容器名
    lines: int = 500


class CommandRunner:
    """外部命令执行，测试可替换。"""

    def run(self, argv: Sequence[str]) -> str:
        completed = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=True,
        )
        return completed.stdout.decode("utf-8", errors="replace")


def collect(specs: List[SourceSpec], runner: CommandRunner = None) -> Dict[str, str]:
    runner = runner or CommandRunner()
    collected: Dict[str, str] = {}
    for spec in specs:
        try:
            collected[spec.name] = _read(spec, runner)
        except Exception as error:  # 采集失败不影响其他来源
            collected[spec.name] = f"[logship] source unavailable: {type(error).__name__}: {error}\n"
    return collected


def _read(spec: SourceSpec, runner: CommandRunner) -> str:
    if spec.kind == "file":
        return _tail_file(Path(spec.location), spec.lines)
    if spec.kind == "docker":
        return runner.run(["docker", "logs", "--tail", str(spec.lines), spec.location])
    raise ValueError(f"unsupported source kind '{spec.kind}'")


def _tail_file(path: Path, lines: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > _READ_TAIL_BYTES:
            handle.seek(size - _READ_TAIL_BYTES)
            handle.readline()  # 丢弃被截断的半行
        payload = handle.read()
    text = payload.decode("utf-8", errors="replace")
    tail = text.splitlines()[-lines:]
    return "\n".join(tail) + "\n" if tail else ""
