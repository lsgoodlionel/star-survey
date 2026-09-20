# publish-gateway（发布网关原型）

把平台的问卷定义发布到 LimeSurvey 引擎，并保证**要么完整上线、要么什么都没发生**。
设计决定与实测证据见 [ADR 0009](../../docs/adr/0009-publish-gateway.md)，
引擎侧的前置事实见 [ADR 0005](../../docs/adr/0005-publishing.md)。

纯标准库，Python 3.9 起可用。

## 链路

```
validate → compile → import → apply → activate → verify → bind
                                ↑                    ↑
                          失败即 delete_survey 回滚 ──┘
```

| 阶段 | 做什么 | 为什么引擎不管 |
|---|---|---|
| `validate` | 题型形状、代码合法性、继承设置、选项缺失 | `activate_survey` 不跑 `checkQuestions()` |
| `compile` | 定义 → `.lss`，所有设置写显式值 | `surveys_groupsettings` 不随 LSS 走 |
| `import` | `import_survey` | — |
| `apply` | 回读问卷设置与题型主题；**激活前**先核对一遍结构 | 导入端会静默改名、静默降级主题 |
| `activate` | `activate_survey`（＋参与者名单） | 导入永远产出未激活问卷 |
| `verify` | 再读一次 `get_fieldmap`，算结构指纹 | 激活本身也可能改变结构 |
| `bind` | 产出绑定记录交给平台存档 | — |

## 用法

```bash
cd platform/tools/publish-gateway
export LIMESURVEY_RPC_PASSWORD=...        # 口令只走环境变量

python3 -m pubgw.cli validate    --definition survey.json
python3 -m pubgw.cli compile     --definition survey.json --out survey.lss
python3 -m pubgw.cli publish     --definition survey.json \
    --engine-url http://localhost --engine-instance survey-prod-a \
    --binding-out binding.json
python3 -m pubgw.cli drift-check --binding binding.json --engine-url http://localhost
```

退出码：`0` 通过；`1` 发布失败／校验不通过／检出漂移；`2` 配置或定义本身有问题。

样例定义：[`platform/tests/fixtures/surveys/publish-gateway.json`](../../tests/fixtures/surveys/publish-gateway.json)。

## 模块

| 模块 | 职责 |
|---|---|
| `model.py` | 定义的解析与不可变数据模型（系统边界） |
| `qtypes.py` | 题型形状表：一道题该产生哪些答卷列 |
| `codes.py` | 引擎的代码合法性规则（镜像自 `Question.php` / `Answer.php`） |
| `validate.py` | 发布前校验，一次性列出全部问题 |
| `compiler.py` | 定义 → `.lss`，并算出编译期结构指纹 |
| `rpc.py` | RemoteControl 客户端，传输层可替换 |
| `fieldmap.py` | `get_fieldmap` 归一化、结构指纹、三段映射 |
| `verify.py` | 回读校验（改名、缺列、顺序） |
| `publish.py` | 七阶段编排与回滚 |
| `binding.py` | 绑定记录 |
| `drift.py` | 发布后的漂移检查 |
| `cli.py` | 命令行入口 |

## 测试

```bash
# 单元测试：不需要运行中的引擎（传输层被替换成假引擎）
cd platform/tools/publish-gateway && python3 -m unittest discover -s tests -t .

# 端到端：真引擎、两种数据库
platform/deploy/test/run-publish-gateway.sh
TEST_DB=pgsql platform/deploy/test/run-publish-gateway.sh
```

## 当前边界

- 只支持 `L ! M P F 1 S T U N D X` 这些题型；其余一律在校验阶段拒绝，
  而不是「放过去再说」。
- 回滚动作是 `delete_survey`，它连答卷一起删，因此**只适用于从未接收过答卷的
  新发布**。已上线问卷的结构升级不能走这条路。
- 指纹只覆盖 `get_fieldmap` 暴露的维度：答案选项的 `code` 变更不在其中。

完整的限制清单见 ADR 0009 的「已知限制与后续」。
