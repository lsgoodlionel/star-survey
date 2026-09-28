"""网关、插件与平台三处**数值上限常量**的一致性。

三处的上限此前靠注释互指（``MjyDictionaryStore.php`` 写着「与平台
``DictionaryLimits.MAX_NODES``、网关 ``MAX_DICTIONARY_NODES`` 一致」），
没有任何自动检查。注释挡不住漂移，而这类漂移全是**静默失败**：

* 网关放得比插件宽——定义过了校验、发布成功，作答时插件才拒；页面上是一个
  没人预料到的报错，而不是编辑时就该看见的「超出上限」；
* 插件放得比平台宽——平台以为自己守住了 1 MiB 的定义信封，引擎侧却收下了
  更大的快照，撑爆的是发布，不是校验；
* 平台放得比网关宽——平台 API 收下的字典／受众，发布时才被网关整份打回，
  而用户已经把它保存成草稿了。

三种都要装好引擎、并且要特定数据才看得见。所以在这里按源码对一遍：
跟着网关单测跑，不需要 PHP，也不需要 JVM（与 ``test_plugin_registry_parity.py`` 同一路数）。

**这份检查自己也要会红**——见 ``LimitParityGoesRedTest``：它把真实对照表里的某一端
喂成人为改过一个数字的源码文本，断言检查必须报出不一致。
"""

import unittest

from .constant_parity import (
    ConstantNotFound, ConstantNotLiteral, DECREASING, GatewayConstant, JavaConstant, LimitGroup,
    PhpConstant, duplicate_names, evaluate_int, mismatches, read_source, readings,
)

#: 三端（或两端）都落地的数值上限。每加一个跨端上限，就在这里加一行。
LIMIT_GROUPS = (
    LimitGroup(
        name="字典节点数上限",
        why="快照随定义下发到引擎，和 1 MiB 的定义信封绑死（ADR 0019 决定 3）",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "MAX_DICTIONARY_NODES"),
            PhpConstant("MjyDictionaryStore.php", "MAX_NODES"),
            JavaConstant("dictionary/DictionaryLimits.java", "MAX_NODES"),
        ),
    ),
    LimitGroup(
        name="字典层级上限",
        why="平台迁移里的 CHECK 约束、网关的级标题校验与插件的快照深度检查是同一条线",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "MAX_DICTIONARY_LEVELS"),
            PhpConstant("MjyDictionaryStore.php", "MAX_DEPTH"),
            JavaConstant("dictionary/DictionaryLimits.java", "MAX_DEPTH"),
        ),
    ),
    LimitGroup(
        name="字典节点标签长度上限",
        why="平台截断、网关放行、插件再截断，三处不同就会出现「存进去的和下发的不一样」",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "MAX_DICTIONARY_LABEL"),
            PhpConstant("MjyDictionaryStore.php", "LABEL_MAX_LENGTH"),
            JavaConstant("dictionary/DictionaryLimits.java", "MAX_LABEL"),
        ),
    ),
    LimitGroup(
        name="字典节点分页上限",
        why="作答页按层分页取节点，插件端点与平台 API 必须给出同样的页大小上限",
        sides=(
            PhpConstant("MjyDictionaryNodesEndpoint.php", "MAX_LIMIT"),
            JavaConstant("dictionary/DictionaryLimits.java", "MAX_PAGE_SIZE"),
        ),
    ),
    LimitGroup(
        name="自增表行数硬上限",
        why="网关按它校验题目属性，插件按它拒收作答；网关宽一点就会放过永远提交不了的题",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "HARD_MAX_ROWS"),
            PhpConstant("MjyRepeatingTableValidator.php", "HARD_MAX_ROWS"),
        ),
    ),
    LimitGroup(
        name="扩展表列数上限",
        why="列定义在网关编译、在插件解析，列数上限不一致就是「发布成功、作答报错」",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "MAX_COLUMNS"),
            PhpConstant("MjyTableColumnSpec.php", "MAX_COLUMNS"),
        ),
    ),
    LimitGroup(
        name="扩展表单元格长度上限",
        why="网关校验 maxLength 的取值范围，插件按同一个数拒收超长单元格",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "CELL_MAX_LENGTH"),
            PhpConstant("MjyRepeatingTableValidator.php", "CELL_MAX_LENGTH"),
        ),
    ),
    LimitGroup(
        name="枚举列取值数上限",
        why="取值集合随 .lss 下发；网关放行的列，插件必须解析得动",
        sides=(
            GatewayConstant("pubgw.questions.theme_kit", "MAX_COLUMN_OPTIONS"),
            PhpConstant("MjyTableColumnSpec.php", "MAX_OPTIONS"),
        ),
    ),
    LimitGroup(
        name="插件通道单次答卷号上限",
        why="网关是唯一的调用方；它敢发的批量必须正好是插件端点肯收的批量（ADR 0018）",
        sides=(
            GatewayConstant("pubgw.channel", "MAX_RESPONSE_IDS"),
            PhpConstant("MjyExtensionAnswerEndpoint.php", "MAX_RESPONSE_IDS"),
        ),
    ),
    LimitGroup(
        name="插件通道单次题目代码上限",
        why="同上：网关多发一个代码，整批读取就被端点整份拒掉",
        sides=(
            GatewayConstant("pubgw.channel", "MAX_QUESTION_CODES"),
            PhpConstant("MjyExtensionAnswerEndpoint.php", "MAX_QUESTION_CODES"),
        ),
    ),
    LimitGroup(
        name="网关读取端点单次答卷号上限",
        why="平台导出按批读作答，批大小上限就是网关读取端点的上限（契约 response-read-v1）",
        sides=(
            GatewayConstant("pubgw.responses", "MAX_RESPONSE_IDS"),
            JavaConstant("response/ResponseExportProperties.java", "MAX_BATCH_SIZE"),
        ),
    ),
    LimitGroup(
        name="签名时间戳偏差窗口",
        why="平台→网关与网关→插件两段签名共用同一个窗口；一边宽一边紧会出现只在边界复现的 401",
        sides=(
            GatewayConstant("pubgw.auth", "MAX_SKEW_SECONDS"),
            PhpConstant("MjyChannelAuth.php", "MAX_SKEW_SECONDS"),
        ),
    ),
    LimitGroup(
        name="平台与网关共享密钥最短字节",
        why="网关拒收短密钥，平台就必须在同一条线上拒绝启动，否则部署到一半才发现",
        sides=(
            GatewayConstant("pubgw.auth", "MIN_SECRET_BYTES"),
            JavaConstant("response/HttpResponseAnswerSource.java", "MIN_SECRET_BYTES"),
            JavaConstant("shared/security/SecurityConfig.java", "MIN_SECRET_BYTES"),
        ),
    ),
    LimitGroup(
        name="插件通道实例密钥最短字节",
        why="派生密钥两端各算一次，长度门槛不同就会出现「网关算得出、插件不认」",
        sides=(
            GatewayConstant("pubgw.channel", "MIN_INSTANCE_SECRET_BYTES"),
            PhpConstant("MjyChannelAuth.php", "MIN_INSTANCE_SECRET_BYTES"),
        ),
    ),
    LimitGroup(
        name="定义信封余量",
        why="平台的定义上限必须严格小于网关的请求体上限，差额留给 requestId、实例标识等信封字段",
        sides=(
            GatewayConstant("pubgw.server", "MAX_BODY_BYTES"),
            JavaConstant("survey/SurveyDefinitions.java", "MAX_DEFINITION_BYTES"),
        ),
        rule=DECREASING,
    ),
)


class LimitParityTest(unittest.TestCase):
    """对照表本身：每一组在各端都读得出来，且满足声明的关系。"""

    def test_the_table_names_every_limit_exactly_once(self):
        self.assertEqual([], duplicate_names(LIMIT_GROUPS))

    def test_every_declared_side_still_exists_in_the_source(self):
        for group in LIMIT_GROUPS:
            with self.subTest(group.name):
                # 读不出来就抛 ConstantNotFound：改名与删除同样是漂移，不能静默跳过。
                self.assertEqual(len(group.sides), len(readings(group)))

    def test_every_limit_agrees_across_the_three_sides(self):
        for group in LIMIT_GROUPS:
            with self.subTest(group.name):
                self.assertEqual([], mismatches(group), "{}（{}）".format(group.name, group.why))


def _rewriting(path, old, new):
    """返回一个 reader：只把 ``path`` 这一份源码里的 ``old`` 换成 ``new``，其余照读。"""

    def reader(wanted):
        source = read_source(wanted)
        if wanted != path:
            return source
        if old not in source:
            raise AssertionError("人为不一致没造出来：{} 里找不到 {!r}".format(wanted, old))
        return source.replace(old, new, 1)

    return reader


class LimitParityGoesRedTest(unittest.TestCase):
    """**证明这份检查会红。**

    用的是真实对照表里的分组与真实源码文本，只把其中一端的一个数字改掉——
    改动只活在这个 reader 里，不落盘。检查必须报出不一致；报不出来，说明
    上面那些 ``assertEqual([], mismatches(...))` 全是摆设。
    """

    #: 拿「字典节点数上限」当样本：它是三端齐全、且此前只靠注释互指的那一条。
    SAMPLE = next(group for group in LIMIT_GROUPS if group.name == "字典节点数上限")

    def test_a_drifted_plugin_constant_is_reported(self):
        side = next(s for s in self.SAMPLE.sides if isinstance(s, PhpConstant))
        reader = _rewriting(side.path, "const MAX_NODES = 8000;", "const MAX_NODES = 8001;")

        problems = mismatches(self.SAMPLE, reader)

        self.assertEqual(1, len(problems), problems)
        self.assertIn("8001", problems[0])
        self.assertIn(side.where, problems[0])

    def test_a_drifted_platform_constant_is_reported(self):
        side = next(s for s in self.SAMPLE.sides if isinstance(s, JavaConstant))
        reader = _rewriting(side.path, "MAX_NODES = 8000;", "MAX_NODES = 7999;")

        problems = mismatches(self.SAMPLE, reader)

        self.assertEqual(1, len(problems), problems)
        self.assertIn("7999", problems[0])

    def test_a_renamed_constant_is_reported_instead_of_skipped(self):
        side = next(s for s in self.SAMPLE.sides if isinstance(s, PhpConstant))
        reader = _rewriting(side.path, "const MAX_NODES = 8000;", "const MAX_NODE_COUNT = 8000;")

        with self.assertRaises(ConstantNotFound):
            mismatches(self.SAMPLE, reader)

    def test_a_missing_source_file_is_reported_instead_of_skipped(self):
        moved = JavaConstant("dictionary/DictionaryLimitsMovedAway.java", "MAX_NODES")

        with self.assertRaises(ConstantNotFound):
            moved.value()

    def test_a_missing_gateway_constant_is_reported_instead_of_skipped(self):
        renamed = GatewayConstant("pubgw.questions.theme_kit", "MAX_DICTIONARY_NODE_COUNT")

        with self.assertRaises(ConstantNotFound):
            renamed.value()

    def test_the_envelope_rule_goes_red_when_the_margin_disappears(self):
        envelope = next(group for group in LIMIT_GROUPS if group.rule == DECREASING)
        inner = next(s for s in envelope.sides if isinstance(s, JavaConstant))
        reader = _rewriting(inner.path, "1024 * 1024 - 4096", "1024 * 1024")

        problems = mismatches(envelope, reader)

        self.assertEqual(1, len(problems), problems)
        self.assertIn("严格大于", problems[0])

    def test_a_group_with_a_single_side_is_rejected(self):
        with self.assertRaises(ValueError):
            LimitGroup(name="一端", why="没有对照就不是一致性检查", sides=(
                GatewayConstant("pubgw.auth", "MAX_SKEW_SECONDS"),
            ))


class ConstantExtractionTest(unittest.TestCase):
    """抽取器读得懂三端实际写法，读不懂时抛而不是猜。"""

    def test_java_underscores_and_the_long_suffix_are_understood(self):
        self.assertEqual(1048576, evaluate_int("1_048_576L", "样例"))

    def test_a_simple_arithmetic_expression_is_folded(self):
        self.assertEqual(1024 * 1024 - 4096, evaluate_int("1024 * 1024 - 4096", "样例"))

    def test_a_value_that_refers_to_another_constant_is_refused(self):
        with self.assertRaises(ConstantNotLiteral):
            evaluate_int("MAX_NODES + 1", "样例")

    def test_a_value_that_does_not_parse_is_refused(self):
        with self.assertRaises(ConstantNotLiteral):
            evaluate_int("new int[]{1", "样例")


if __name__ == "__main__":
    unittest.main()
