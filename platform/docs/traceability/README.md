# 需求追溯（R23-12）

R23-12 要的是「需求变更与全量验收证据」。在这之前仓库里只有约 160 个自有文件里的
零散 `R05-…`／`R23-…` 注释，没有任何需求↔测试的关联物，也没有任何东西阻止这些注释腐化。
这个目录补的就是那一块。

## 两份文件

| 文件 | 是什么 | 谁维护 |
|---|---|---|
| [requirement-index.md](requirement-index.md) | 305 条需求编号的仓库内快照，带矩阵原文 SHA-256 | 机器生成，勿手改 |
| [requirement-tests.md](requirement-tests.md) | 需求↔测试的登记表 | 人工维护，机器校验 |

需求定义与验收断言的**唯一真源**仍然是仓库外的 `02-全量需求追踪矩阵.md`。
快照存在的唯一理由是 CI 的检出里读不到那份文档，而编号校验必须在 CI 里生效。

## 跑法

```bash
cd platform/tools/traceability

python3 -m reqtrace.cli check       # 六道闸门；非零退出＝有漂移
python3 -m reqtrace.cli report      # 按工作包的覆盖统计（只读）
python3 -m reqtrace.cli sync        # 改了 02 矩阵之后重生成快照

python3 -m unittest discover -s tests -t . -q   # 校验器自己的用例
```

矩阵不在默认路径时用环境变量指过去：

```bash
REQUIREMENT_MATRIX=/path/to/02-全量需求追踪矩阵.md python3 -m reqtrace.cli check
```

## 六道闸门

| # | 查什么 | 对应的现实漂移 |
|---|---|---|
| 1 | 登记表的格式 | 列数写错、层级自创用词、定位符写成一句话 |
| 2 | 登记的编号在索引里 | 手抖把 `R01-02` 写成 `R01-20` |
| 3 | 登记的定位符指向真实存在的测试 | **测试改名或删掉**，而登记表继续声称覆盖 |
| 4 | 自有源码里的编号都在索引里 | 注释里凭空写了个不存在的编号 |
| 5 | 题型主题声明的需求有测试证据 | 加了主题只写实现不补证据 |
| 6 | 快照与仓库外的 02 矩阵一致 | 矩阵改了而快照没重生成 |

第 6 道在矩阵读不到时**明确跳过**（CI 上会打印「跳过」而不是「已对」）；前五道任何环境都生效。

## 这套东西**不**做什么

- **不执行任何测试。** 它只回答「声称的那个测试是否还在」。要在没有 JVM、没有 PHP、
  没有引擎栈的 runner 上秒级跑完，所以按源码文本核对符号，不启动任何测试框架。
  「测试是否通过」由各自的套件回答。
- **Java／PHP 不查层级。** 正则只能给出「文件里出现过哪些名字」的平表。因此这两种后缀
  的定位符**只接受一级符号**——写成两级会被当成格式错误拒绝，而不是假装查过了。
  Python 走 AST，层级是真查的。
- **第六道闸门在 CI 上等于没有。** 02 矩阵在仓库外，CI 读不到，所以「快照有没有跟上矩阵」
  只能靠本地跑一次（或让改矩阵的人跑 `sync`）。快照头部记着矩阵原文的 SHA-256，
  至少让「它是照着哪一版生成的」可审计。
- **不等于验收通过。** 登记表证明「有测试且测试还在」；矩阵里那条需求的全部子项是否满足，
  见 `08-需求进度与方案修订（仓库实证）.md`。表里好几条（R04-02／03／05、R06-06）
  本来就是**部分**覆盖，说明列里写明了口径。
- **不是 305 条的完整映射。** 当前只登记了确有测试证据、且证据逐条核对过的部分
  （37 条需求、80 条证据）。覆盖率用 `report` 子命令看，不要从「校验通过」推断「都覆盖了」。

## 校验器凭什么可信

每一道闸门都有一条**人为制造不一致、断言它确实报错**的用例，在
`platform/tools/traceability/tests/` 里：

- `test_locators.py::PythonLocatorTest::test_a_renamed_method_is_not_found` —— 测试改名
- `test_locators.py::PythonLocatorTest::test_a_method_moved_to_another_class_is_not_found` —— 方法换了类
- `test_locators.py::JavaLocatorTest::test_a_control_flow_keyword_is_not_a_method` —— `} else if (…) {` 曾被认成方法
- `test_locators.py::JavaLocatorTest::test_an_anonymous_class_type_is_not_a_method` —— `new Runnable() {` 曾被认成方法
- `test_check.py::GatesGoRedTest::test_a_new_theme_without_test_evidence_is_red` —— 新主题没补证据
- `test_check.py::GatesGoRedTest::test_an_invented_requirement_id_in_a_code_comment_is_red` —— 注释里的错编号
- `test_check.py::GatesGoRedTest::test_a_stale_snapshot_is_red` —— 快照过期
- `test_index.py::ReconcileGoesRedTest` —— 矩阵增删条目、改措辞

另有三条**哨兵**用例，专门防「因为扫不到所以永远绿」这种假绿：
`test_references.py::ScopeTest::test_the_scan_actually_looks_at_something`、
`test_check.py::RepositoryTest::test_the_registry_actually_claims_something`、
以及 `code_claims.EXPECTED_AT_LEAST` 那个下限哨兵。
