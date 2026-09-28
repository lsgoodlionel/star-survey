"""证据定位符的解析与核对。

核心的那条红用例是 ``test_a_renamed_method_is_not_found``：**测试改名**是这套映射最
常见的腐化方式——改名的人不会想到去改一张文档表格，于是登记表继续声称覆盖，而那个
测试已经不叫那个名字了。一个不会因此变红的校验，就只是一张好看的表。
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from reqtrace.locators import parse_locator, resolve

PY_SOURCE = '''
def scenario_password(context):
    pass


class LoopRatingTest:
    def test_the_row_count_is_pinned(self):
        pass


class ShelfTest:
    def test_the_quantity_column_is_bounded(self):
        pass
'''

JAVA_SOURCE = """
package cn.mjy.platform.delivery;

class DeliveryLinkTest {
    @Nested
    class ShortLinks {
        @Test
        void theShortLinkResolvesToTheSameRespondentUrl() {
        }
    }

    @Test
    void aTamperedParameterNoLongerVerifies() throws Exception {
        if (first) {
            one();
        } else if (second) {
            two();
        }
        while (more) { three(); }
        try { four(); } catch (Exception ignored) { five(); }
        Runnable task = new Runnable() {
            @Override
            public void run() {
            }
        };
        register(() -> new Callback() {
            public void onDone() {
            }
        });
    }

    private Map<String, List<Integer>> grouped(List<String> codes) throws IOException {
        return null;
    }
}
"""

PHP_SOURCE = """<?php
class MjyDictionaryPathTest extends TestCase
{
    public function testRejectsABrokenPath(): void
    {
    }
}
"""


class ParsingTest(unittest.TestCase):
    def test_a_path_with_two_symbols_is_parsed(self):
        locator = parse_locator("a/b/test_x.py::ClassName::test_method")
        self.assertEqual("a/b/test_x.py", locator.path)
        self.assertEqual(("ClassName", "test_method"), locator.symbols)

    def test_a_bare_script_path_is_parsed(self):
        self.assertEqual((), parse_locator("platform/deploy/test/run-x.sh").symbols)

    def test_an_absolute_path_is_refused(self):
        with self.assertRaises(ValueError):
            parse_locator("/etc/passwd::x")

    def test_a_path_escaping_the_repository_is_refused(self):
        with self.assertRaises(ValueError):
            parse_locator("../../elsewhere/test_x.py::X::test_y")

    def test_an_unknown_suffix_is_refused_rather_than_silently_accepted(self):
        """接受一个不认识的后缀＝那条证据永远查不到符号，也就是永远绿。"""
        with self.assertRaises(ValueError):
            parse_locator("platform/docs/somewhere.txt::X")

    def test_three_levels_of_symbol_are_refused(self):
        with self.assertRaises(ValueError):
            parse_locator("a/test_x.py::A::B::c")

    def test_a_two_level_java_locator_is_refused_because_hierarchy_is_unverifiable(self):
        """Java 只能拿到平表。接受 `Foo.java::SomeClass::someMethod` 就等于暗示
        「方法真的在这个类里」被核对过——而它没有。宁可说不会，不要假装会。"""
        with self.assertRaises(ValueError) as caught:
            parse_locator("a/FooTest.java::ShortLinks::theLinkResolves")
        self.assertIn("只接受一级符号", str(caught.exception))

    def test_a_two_level_php_locator_is_refused_for_the_same_reason(self):
        with self.assertRaises(ValueError):
            parse_locator("a/FooTest.php::FooTest::testSomething")

    def test_a_two_level_python_locator_is_still_accepted(self):
        """Python 走 AST，层级是真的查得了的，所以这一级不该被顺手收紧。"""
        self.assertEqual(("A", "test_b"), parse_locator("a/test_x.py::A::test_b").symbols)

    def test_a_prose_fragment_is_not_a_symbol(self):
        with self.assertRaises(ValueError):
            parse_locator("a/test_x.py::见 ADR 0016 第三节")


class _RepoCase(unittest.TestCase):
    """在临时 git 仓库里造出几个文件：可执行位要从 git 索引读，所以必须真的是 git 仓库。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        (self.root / "tests").mkdir()
        (self.root / "tests/test_sample.py").write_text(PY_SOURCE, encoding="utf-8")
        (self.root / "tests/SampleTest.java").write_text(JAVA_SOURCE, encoding="utf-8")
        (self.root / "tests/SampleTest.php").write_text(PHP_SOURCE, encoding="utf-8")
        (self.root / "tests/run-sample.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (self.root / "tests/run-forgotten.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (self.root / "tests/run-sample.sh").chmod(0o755)
        self._git("init", "-q")
        self._git("add", "-A")

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=str(self.root), check=True,
                       capture_output=True, text=True)

    def _resolve(self, raw):
        return resolve(parse_locator(raw), self.root)


class PythonLocatorTest(_RepoCase):
    def test_a_class_and_method_that_exist_resolve_cleanly(self):
        self.assertEqual([], self._resolve("tests/test_sample.py::LoopRatingTest::test_the_row_count_is_pinned"))

    def test_a_top_level_function_resolves(self):
        self.assertEqual([], self._resolve("tests/test_sample.py::scenario_password"))

    def test_a_renamed_method_is_not_found(self):
        """最常见的腐化：测试改了名，登记表还在声称覆盖。"""
        problems = self._resolve("tests/test_sample.py::LoopRatingTest::test_the_row_count_is_pinned_to_objects")
        self.assertEqual(1, len(problems))
        self.assertIn("找不到", problems[0])

    def test_a_method_moved_to_another_class_is_not_found(self):
        """方法还在文件里，但不在声称的那个类里——层级必须真的对上。"""
        problems = self._resolve("tests/test_sample.py::ShelfTest::test_the_row_count_is_pinned")
        self.assertEqual(1, len(problems))

    def test_a_deleted_file_is_reported(self):
        problems = self._resolve("tests/test_gone.py::X::test_y")
        self.assertEqual(["tests/test_gone.py::X::test_y：文件不存在"], problems)

    def test_a_source_locator_without_a_symbol_is_reported(self):
        """只写到文件名等于没说清覆盖点，而且文件在就永远绿。"""
        problems = self._resolve("tests/test_sample.py")
        self.assertEqual(1, len(problems))
        self.assertIn("必须指到具体的测试符号", problems[0])


class JavaLocatorTest(_RepoCase):
    def test_a_method_inside_a_nested_class_is_found(self):
        self.assertEqual([], self._resolve("tests/SampleTest.java::theShortLinkResolvesToTheSameRespondentUrl"))

    def test_a_method_that_throws_is_found(self):
        self.assertEqual([], self._resolve("tests/SampleTest.java::aTamperedParameterNoLongerVerifies"))

    def test_a_generic_return_type_and_throws_clause_do_not_hide_a_method(self):
        self.assertEqual([], self._resolve("tests/SampleTest.java::grouped"))

    def test_a_renamed_java_method_is_not_found(self):
        problems = self._resolve("tests/SampleTest.java::theShortLinkResolves")
        self.assertEqual(1, len(problems))

    def test_a_control_flow_keyword_is_not_a_method(self):
        """`} else if (cond) {` 曾被认成「类型 else、方法名 if」——真实误判，
        它让 `Foo.java::if` 这种无意义的定位符解析得通，也就是一种假绿。"""
        self.assertEqual(1, len(self._resolve("tests/SampleTest.java::if")))

    def test_an_anonymous_class_type_is_not_a_method(self):
        """`new Runnable() {` 曾把被实例化的类型名当成方法名。"""
        self.assertEqual(1, len(self._resolve("tests/SampleTest.java::Runnable")))

    def test_an_anonymous_class_after_another_keyword_is_not_a_method_either(self):
        """`new` 可能出现在类型段中间（`... -> new Callback() {`）——挡的位置要紧贴名字。"""
        self.assertEqual(1, len(self._resolve("tests/SampleTest.java::Callback")))

    def test_the_methods_of_an_anonymous_class_are_still_found(self):
        """挡掉类型名不该连里面真实的方法一起挡掉。"""
        self.assertEqual([], self._resolve("tests/SampleTest.java::run"))
        self.assertEqual([], self._resolve("tests/SampleTest.java::onDone"))


class PhpLocatorTest(_RepoCase):
    def test_a_php_method_is_found(self):
        self.assertEqual([], self._resolve("tests/SampleTest.php::testRejectsABrokenPath"))

    def test_a_renamed_php_method_is_not_found(self):
        self.assertEqual(1, len(self._resolve("tests/SampleTest.php::testRejectsBrokenPaths")))


class ScriptLocatorTest(_RepoCase):
    def test_an_executable_script_resolves(self):
        self.assertEqual([], self._resolve("tests/run-sample.sh"))

    def test_a_script_without_the_executable_bit_is_reported(self):
        problems = self._resolve("tests/run-forgotten.sh")
        self.assertEqual(1, len(problems))
        self.assertIn("可执行位", problems[0])

    def test_a_script_locator_with_a_symbol_is_reported(self):
        problems = self._resolve("tests/run-sample.sh::scenario_password")
        self.assertIn("不接符号", problems[0])


if __name__ == "__main__":
    unittest.main()
