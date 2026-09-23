"""网关、插件与平台读端三处「注册表」的一致性：少注册一项在引擎上是**静默**故障。

三处漂移各自的后果：

* 主题名没进 ``MjyThemedQuestionMap::STRUCTURED_THEMES``——插件根本不认这道题，
  JSON 信封无人校验、副表一行不写，而页面照常渲染、答卷照常提交；
* 主题名没进 ``QuestionTypeColumns.STRUCTURED_THEMES``——字段字典把那一列整块 JSON
  当成普通取值列，导出表头与标签都是错的；
* 属性名没进 ``MjyQuestionAttributeDefinitions``——``QuestionAttribute::filterXss()``
  会净化它，JSON 的引号与 URL 的 ``&`` 被改写，列定义解析失败。

三种都要装好引擎才看得见，所以在这里按源码文本对一遍：这份用例不需要 PHP 与 JVM，
跟着网关单测一起跑。
"""

import re
import unittest
from pathlib import Path

from pubgw.questions.themes import MANAGED_ATTRIBUTES, STRUCTURED_THEMES

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO_ROOT / "plugins/MjyQuestionExtensions"
THEME_MAP = PLUGIN_ROOT / "MjyThemedQuestionMap.php"
ATTRIBUTE_DEFINITIONS = PLUGIN_ROOT / "MjyQuestionAttributeDefinitions.php"
FIELD_DICTIONARY = (REPO_ROOT / "platform/services/business/src/main/java"
                    / "cn/mjy/platform/response/QuestionTypeColumns.java")

#: 由引擎自己认识、不需要插件注册的属性（原生属性，或分组主题纯展示用的那个）。
_ENGINE_OWNED = frozenset({"commented_checkbox"})


def _php_source(path):
    return path.read_text(encoding="utf-8")


def _structured_themes_in_plugin():
    """``const STRUCTURED_THEMES = [self::A, self::B];`` 里每个常量的取值。"""
    source = _php_source(THEME_MAP)
    block = re.search(r"const STRUCTURED_THEMES = \[(.*?)\];", source, re.S)
    names = re.findall(r"self::(\w+)", block.group(1)) if block else []
    values = dict(re.findall(r"const (\w+) = '([^']+)';", source))
    return [values[name] for name in names if name in values]


def _declared_attribute_names():
    return set(re.findall(r"const \w+ = '(mjy_\w+)';", _php_source(ATTRIBUTE_DEFINITIONS)))


def _structured_themes_in_field_dictionary():
    """``Set.of("mjy-…", "mjy-…")`` 里的主题名。"""
    source = _php_source(FIELD_DICTIONARY)
    block = re.search(r"STRUCTURED_THEMES\s*=\s*java\.util\.Set\.of\((.*?)\);", source, re.S)
    return re.findall(r'"(mjy-[\w-]+)"', block.group(1)) if block else []


class StructuredThemeParityTest(unittest.TestCase):
    def test_the_plugin_lists_the_same_structured_themes(self):
        self.assertEqual(sorted(STRUCTURED_THEMES), sorted(_structured_themes_in_plugin()))

    def test_the_field_dictionary_lists_the_same_structured_themes(self):
        self.assertEqual(sorted(STRUCTURED_THEMES), sorted(_structured_themes_in_field_dictionary()))


class ManagedAttributeParityTest(unittest.TestCase):
    def test_every_managed_attribute_is_declared_by_the_plugin(self):
        expected = {name for name in MANAGED_ATTRIBUTES if name not in _ENGINE_OWNED}
        self.assertEqual(set(), expected - _declared_attribute_names())
