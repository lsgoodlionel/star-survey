# P0-00.2 上游差异比对

- 日期：2026-09-20
- 关联：[ADR 0001](../adr/0001-engine-baseline.md) 待办第 2 条、[ADR 0004](../adr/0004-single-repository.md)
- 结论：**已取得完整结果**。我们的引擎快照与上游逐文件完全一致，自有内容全部是新增文件。

---

## 1. 先纠正一个前提：上游没有 7.1.2 这个发布

`application/config/version.php` 写的是：

```php
$config['versionnumber'] = '7.1.2';
$config['dbversionnumber'] = 714;
$config['buildnumber'] = '';      // 空
```

查 `LimeSurvey/LimeSurvey` 的 tag 列表，7.x 最新的发布是 **`7.1.1+260914`**，
**没有任何名为 `7.1.2` 的 tag**（`refs/tags/7.1.2` 返回 404）。

上游的惯例是：发布时把 `buildnumber` 填上并打 `<版本>+<build>` 的 tag；
发布之后立刻在 master 上把 `versionnumber` 递增、`buildnumber` 清空。
**`buildnumber` 为空 = 这是两次发布之间的 master 开发态，不是一个发布版本。**

所以「与上游 7.1.2 比对」这个任务本身没有可比对象。可比的、也是正确的基线是
我们 fork 时所在的那个上游提交：

| 项 | 值 |
|---|---|
| 基线提交 | `4c20c68033c8e37140f26af80a65f659b40f8a45` |
| 在上游仓库中存在 | 是（`LimeSurvey/LimeSurvey`，非 fork 独有） |
| 提交时间 | 2026-09-17T23:04:58Z |
| 提交信息 | `Dev Merge branch 'v6.x'` |
| 距最近发布 tag `7.1.1+260914` | **领先 108 个提交，涉及 278 个文件**；落后 0 个提交 |

---

## 2. 方法

### 2.1 失败的路径（如实记录）

| 尝试 | 结果 |
|---|---|
| `git fetch --unshallow limesurvey-fork` | **失败**。跑了约 25 分钟，288,653 个对象只收到 8%（约 6 MiB），速率从 17 KiB/s 一路掉到 8 KiB/s，最后 `fetch-pack: unexpected disconnect while reading sideband packet` / `fatal: early EOF` |
| 下载 7.1.2 release 压缩包 | **失败**，`404: Not Found` —— 见第 1 节，该 tag 不存在 |
| 匿名 REST API 递归取 tree | **失败**。单个响应约 10 MB，连续 3 次都在 0.4–3.2 MB 处被截断（`Transferred a partial file`） |
| 匿名 REST API 逐目录取 tree | **失败**。请求变小了确实不再截断，但未认证配额只有 60 次/小时，46 个目录之后 `403 rate limit exceeded` |

### 2.2 成功的路径

改用已登录的 `gh`（配额 5000 次/小时）一次性取回整棵树的**对象哈希清单**，
再和本地 `git ls-tree` 的哈希逐条比对。关键在于 **不需要下载任何文件内容**：
git 的 blob SHA-1 是内容的哈希，哈希相同即内容逐字节相同。整个比对只花了一次
API 请求、约 3 MB 传输量 —— 这是在这种网络条件下唯一跑得通的办法。

```bash
# 上游侧：一次请求拿到 22,073 个 blob 的 (sha, path)
gh api "repos/LimeSurvey/LimeSurvey/git/trees/4c20c68033c8e37140f26af80a65f659b40f8a45?recursive=1"

# 本地侧：注意用 -z，LimeSurvey 有带空格的路径（如 "assets/fonts/DejaVu Fonts License.txt"），
# 按空白切分会把它们切坏，制造出假的差异
git ls-tree -r -z <commit>
```

---

## 3. 结果

### 3.1 我们的根提交 vs 上游 `4c20c680`

根提交 `836af0f8`（`vendor: LimeSurvey 7.1.2 snapshot`）：

| 项 | 数量 |
|---|---|
| 上游文件数 | 22,073 |
| 我们的文件数 | 22,073 |
| 仅上游有 | **0** |
| 仅我们有 | **0** |
| 内容不同 | **0** |

**逐文件完全一致。fork 是上游的干净快照，没有夹带任何私货。**

### 3.2 我们的 HEAD vs 上游 `4c20c680`

| 项 | 数量 | 明细 |
|---|---|---|
| 被我们修改的文件 | **1** | `.gitignore` |
| 被我们删除的文件 | **0** | —— |
| 被我们新增的文件 | **132** | `platform/` 92、`plugins/Mjy*` 35、`themes/question/mjy-repeating-table` 4、仓库根 1（`docker-compose.dev.yml`） |

唯一一处对上游文件的改动是 `.gitignore` 的两行新增，用来让自研插件不被上游的
`/plugins/*` 忽略规则挡掉：

```diff
 /plugins/*
 !/plugins/index.html
 !/plugins/Demo/
+# MJY platform plugins (see platform/docs/adr/0004-single-repository.md)
+!/plugins/Mjy*/
```

**结论：引擎源码（`application/`、`tests/`、`themes/`（自有主题目录除外）、`vendor/`、`assets/`）
零改动。**

---

## 4. 对 fork 维护的意义

1. **升级成本目前接近于零。** 没有需要重新套用的核心补丁，升级上游等于换一批文件再把
   132 个自有文件放回去。冲突面只有 `.gitignore` 一行规则。
2. **这个性质是要守住的，不是自然成立的。** 一旦有人为了赶工改了 `application/` 下的文件，
   上面这张表就会长出第二行，而且以后每次升级都要还一次债。建议把
   「HEAD 相对上游基线的 modified 文件数必须为 1（只有 `.gitignore`）」做成可执行的检查，
   命令就是第 2.2 节那两条，不依赖完整 git 历史，网络差也能跑。
3. **基线是 master 开发态，不是发布版。** `4c20c680` 领先 `7.1.1+260914` 有 108 个提交，
   这些提交没有经过上游的发布流程。风险不在差异本身（我们和它一模一样），
   而在于：将来要升级时，参照物是「上游 master 的某个点」而不是「某个发布版本」，
   回归范围不好界定。**建议在进入 P1 之前把引擎基线挪到一个真正的发布 tag 上**，
   届时再用同样的方法确认差异仍然只有 `.gitignore`。
4. **浅克隆仍然是个限制。** 本次绕开了它，但绕开的方式只能比对「树」，比对不了「历史」。
   要回答「上游从 X 到 Y 改了什么、为什么改」这类问题，还是得有完整历史，
   而当前网络条件下 `--unshallow` 做不到（第 2.1 节）。
