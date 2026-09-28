"""本项目自有的 shell 脚本必须带可执行位。

为什么要用测试钉住：这个坑犯过两次。`run-publish-gateway-parity.sh` 因为是 100644，
在 CI 里直接 permission denied 退 126——这很可能就是它长期没接上 CI 的真正原因；修它的
那条车道紧接着又给 `run-platform-tests.sh` 留了同样的毛病。一次全仓扫描发现 platform/
下有 20 个脚本缺位，包括 deploy/exam、deploy/functional、deploy/private、deploy/tenancy
四整套入口和私有化的 backup/restore。

靠人记不住，所以交给机器。上游自带的脚本不在管辖范围内（改它们会破坏「自有内容全是
新增文件」这个不变式，见 docs/p0/upstream-diff.md）。
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
#: 只管本项目自有的路径；其余属于上游快照。
OWNED_PREFIXES = ("platform/", "plugins/Mjy", "themes/question/mjy-")


def tracked_shell_scripts():
    """git 索引里的 *.sh 及其模式，按 (模式, 路径) 返回。"""
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", "*.sh"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        mode, _, _, path = line.split(maxsplit=3)
        yield mode, path


class OwnedScriptsAreExecutableTest(unittest.TestCase):
    def test_every_owned_shell_script_has_the_executable_bit(self):
        offenders = [
            path for mode, path in tracked_shell_scripts()
            if path.startswith(OWNED_PREFIXES) and mode != "100755"
        ]
        self.assertEqual([], offenders, "这些自有脚本缺可执行位，在 CI 里会 permission denied")

    def test_the_scan_actually_looks_at_something(self):
        """防止上面那条因为路径前缀写错而永远为空——那就是个假绿。"""
        owned = [p for _, p in tracked_shell_scripts() if p.startswith(OWNED_PREFIXES)]
        self.assertGreater(len(owned), 20, "扫不到自有脚本，前缀大概写错了")


class NoBuildArtefactsAreTrackedTest(unittest.TestCase):
    """构建产物不该进仓库。

    这条也是踩出来的：我在容器里用 Python 3.11 跑测试后 `git add -A`，把 135 个
    `__pycache__/*.pyc` 一并提交了，而 `.gitignore` 当时根本没有 Python 的规则——
    网关与工具链都是 Python，这个口子一直开着，只是此前没人在仓库里生成过字节码。
    """

    #: 一眼就能判定「绝不该被跟踪」的产物。按**后缀**判，不按子串——上游有
    #: `pChart.class.php`、`xlsxwriter.class.php` 这类文件，子串匹配会误伤它们。
    FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".class")

    def test_no_python_or_java_bytecode_is_tracked(self):
        out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout
        offenders = [p for p in out.splitlines()
                     if p.endswith(self.FORBIDDEN_SUFFIXES) or "__pycache__/" in p]
        self.assertEqual([], offenders[:20], "构建产物被跟踪了，应当进 .gitignore")

    def test_the_scan_actually_looks_at_something(self):
        """防止上面那条因为 git 调用失败而永远为空——那就是个假绿。"""
        out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout
        self.assertGreater(len(out.splitlines()), 1000, "扫不到文件，git 调用大概出了问题")
