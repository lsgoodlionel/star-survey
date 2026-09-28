"""证据定位符：把「某条需求由某个测试覆盖」这句话解析成可以去仓库里核对的东西。

一套语法通四种语言，因为四种测试都在用：

    <仓库相对路径>[::<符号>[::<符号>]]

    platform/tools/publish-gateway/tests/test_question_themes.py::CollapsibleTest::test_column_shape_is_unchanged
    platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryLinkTest.java::theShortLinkResolvesToTheSameRespondentUrl
    plugins/MjyQuestionExtensions/tests/MjyDictionaryPathTest.php::testRejectsABrokenPath
    platform/tests/e2e/access_policy.py::scenario_password
    platform/deploy/test/run-access-policy.sh

解析方式按后缀分：

* ``.py`` 走 AST（``ast`` 是标准库，3.9 与 3.11 都在）。类里的方法要求**层级正确**，
  ``Foo::test_bar`` 里的 ``test_bar`` 必须真的在 ``Foo`` 里，挪到别的类就算没找到；
* ``.java`` / ``.php`` 用正则找声明。不解析完整语法——只要能抓住「改名／删掉」这两种
  真实会发生的漂移就够了，装 JVM 与 PHP 来做这件事不值得。代价是**查不了层级**：
  正则只能给出「这个文件里出现过哪些名字」的一张平表。所以这两种后缀**只接受一级符号**，
  写成 ``Foo.java::SomeClass::someMethod`` 会被当成格式错误而拒绝——接受那个形状就等于
  暗示层级被核对过，而它没有。Java 的方法名在一个测试文件里本来就唯一得足够定位；
* ``.sh`` 不带符号：脚本是整体入口，同时查可执行位（缺了在 CI 里就是 126）。

刻意**不做**的事：不执行任何测试。这个校验要能在没有 JVM、没有 PHP、没有引擎栈的
runner 上秒级跑完；「测试是否通过」由各自的套件回答，这里只回答「它是否还在」。
"""

import ast
import re
import subprocess
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Set, Tuple

#: 方法名允许的形状：够严，能挡住把整行说明当符号写进来。
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 认识的后缀。别的后缀一律当写错——沉默地接受一个不认识的后缀就是假绿。
_SUPPORTED_SUFFIXES = (".py", ".java", ".php", ".sh")

#: 只能拿到「文件里出现过哪些名字」这张平表的后缀，因此只接受一级符号。
_FLAT_SUFFIXES = (".java", ".php")


class Locator(NamedTuple):
    """一条证据定位符。``symbols`` 最多两级（类、方法）。"""

    raw: str
    path: str
    symbols: Tuple[str, ...]

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix


def parse_locator(raw: str) -> Locator:
    """解析一条定位符；形状不对直接抛，不要留给后面去猜。"""
    text = raw.strip()
    if not text:
        raise ValueError("定位符是空的")
    parts = text.split("::")
    path = parts[0].strip()
    symbols = tuple(part.strip() for part in parts[1:])
    if not path:
        raise ValueError("定位符 {!r} 没有路径".format(raw))
    if path.startswith("/") or ".." in Path(path).parts:
        raise ValueError("定位符 {!r} 必须是仓库相对路径".format(raw))
    if Path(path).suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            "定位符 {!r} 的后缀不在 {} 里".format(raw, "／".join(_SUPPORTED_SUFFIXES))
        )
    if len(symbols) > 2:
        raise ValueError("定位符 {!r} 的符号超过两级".format(raw))
    # Java／PHP 只能拿到一张平表，核对不了「方法真的在这个类里」。接受两级就等于
    # 暗示层级被核对过，所以直接拒绝——宁可说不会，不要假装会。
    if len(symbols) == 2 and Path(path).suffix in _FLAT_SUFFIXES:
        raise ValueError(
            "定位符 {!r}：{} 只接受一级符号（层级核对不了，写成两级会误导）".format(
                raw, Path(path).suffix
            )
        )
    for symbol in symbols:
        if not _SYMBOL.match(symbol):
            raise ValueError("定位符 {!r} 里的符号 {!r} 不像一个标识符".format(raw, symbol))
    return Locator(raw=text, path=path, symbols=symbols)


# ------------------------------------------------------------------ 各语言的符号查找


def _python_symbols(source: str, path: str) -> Set[Tuple[str, ...]]:
    """Python 源码里所有可定位的 ``(类,)`` 与 ``(类, 方法)``、以及顶层 ``(函数,)``。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError("{} 解析不了：{}".format(path, error))
    found: Set[Tuple[str, ...]] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add((node.name,))
        elif isinstance(node, ast.ClassDef):
            found.add((node.name,))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add((node.name, child.name))
    return found


#: 不是方法名的控制流关键字。方法声明的正则会把 `} else if (cond) {` 认成
#: 「类型 else、方法名 if」——探测出来的真实误判，扣掉它们才不会让 `Foo.java::if`
#: 这种无意义的定位符「解析得通」。剔除在**捕获之后**做，比把关键字塞进正则更好读。
_JAVA_NON_METHODS = frozenset({
    "if", "else", "for", "while", "do", "switch", "case", "default",
    "try", "catch", "finally", "synchronized", "return", "throw", "assert",
})

#: 方法声明：`<类型…> <名字>(…) [throws …] {`。
#: 名字前面那个 `(?<!\bnew\s)` 挡住匿名类——`new Runnable() {` 会把被实例化的
#: **类型名**当成方法名，同样是探测出来的真实误判。它必须紧贴捕获组：类型那一段允许含
#: 空格（`public void`），所以 `new` 可能出现在段中间（`return new Runnable() {`），
#: 放在整条正则开头是挡不住的。
_JAVA_METHOD = re.compile(
    r"\b\w[\w<>\[\],.?& ]*\s+(?<!\bnew\s)(\w+)\s*\([^;{]*\)\s*(?:throws [\w., ]+)?\{"
)
_JAVA_TYPE = re.compile(r"\b(?:class|interface|record|enum)\s+(\w+)")


def _java_members(source: str) -> Set[str]:
    """Java 源码里的类／嵌套类名与方法名，**平表**（查不了层级，见模块开头）。

    ``@Nested`` 不必特殊处理：嵌套类与它的方法名照样都在表里。
    """
    names = set(_JAVA_TYPE.findall(source))
    names |= set(_JAVA_METHOD.findall(source))
    return names - _JAVA_NON_METHODS


def _php_members(source: str) -> Set[str]:
    names = set(re.findall(r"\bclass\s+(\w+)", source))
    names |= set(re.findall(r"\bfunction\s+(\w+)\s*\(", source))
    return names


# ------------------------------------------------------------------ 核对


def _executable_paths(repo_root: Path) -> Set[str]:
    """git 索引里带可执行位（100755）的路径。用索引而不是文件系统：
    提交进去的模式才是 CI 上会生效的那个。"""
    out = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    ).stdout
    executable = set()
    for line in out.splitlines():
        mode, _, _, path = line.split(maxsplit=3)
        if mode == "100755":
            executable.add(path)
    return executable


def resolve(locator: Locator, repo_root: Path, executable: Optional[Set[str]] = None) -> List[str]:
    """核对一条定位符，返回问题列表（空＝这条证据确实在）。"""
    problems: List[str] = []
    target = repo_root / locator.path
    if not target.is_file():
        return ["{}：文件不存在".format(locator.raw)]

    if locator.suffix == ".sh":
        if locator.symbols:
            problems.append("{}：脚本定位符不接符号".format(locator.raw))
        known = executable if executable is not None else _executable_paths(repo_root)
        if locator.path not in known:
            problems.append("{}：脚本缺可执行位，在 CI 里会 permission denied".format(locator.raw))
        return problems

    if not locator.symbols:
        problems.append("{}：源码定位符必须指到具体的测试符号".format(locator.raw))
        return problems

    source = target.read_text(encoding="utf-8")
    if locator.suffix == ".py":
        if locator.symbols not in _python_symbols(source, locator.path):
            problems.append("{}：找不到 {}".format(locator.raw, "::".join(locator.symbols)))
        return problems

    members = _java_members(source) if locator.suffix == ".java" else _php_members(source)
    for symbol in locator.symbols:
        if symbol not in members:
            problems.append("{}：找不到 {}".format(locator.raw, symbol))
    return problems


def resolve_all(locators: Sequence[Locator], repo_root: Path) -> List[str]:
    """一次核对一批；``git ls-files`` 只跑一次。"""
    executable = _executable_paths(repo_root)
    problems: List[str] = []
    for locator in locators:
        problems.extend(resolve(locator, repo_root, executable))
    return problems
