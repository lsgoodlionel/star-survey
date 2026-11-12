# Wave 6 受控恢复与清理实施计划

基线：main `825e7795c956c453c2cb99605a9e66367f7ed2f1`；对应 #33。Release 工作流由 Claude Code 处理，本计划不修改、触发或解除它的门禁。只使用 Codex 原生工具及仓库现有测试，不引入新服务、权限或持久凭据。

## 已确认问题与边界

rc20 run42790836656 的四个 Native job 都在 `Block native lifecycle before host mutation` 主动失败；安装和恢复从未执行。旧 rc19 的历史成功不能证明当前 main 安全。#99/#102/#103 保留的阻断是有意保护：锁释放、正常健康、旧进程退出码或一次目录扫描，都不足以证明旧 helper、外部写入者、数据和明文已经安全收束。

沿用原单机生产计划的既定方向：恢复到**新的目录、Compose 项目和持久卷**，验证后再单独决定切换。原实例、备份、`operation.intent` 和 `recovery_required` 不因计划生成或新副本恢复而被覆盖或清除。备份之后的写入仍保存在原实例，不能把它们当作已恢复进新副本；生产切换和数据取舍另需明确批准。

## 切片 1：只读计划与现场绑定（#108 已合并）

新增 `surveyctl recovery-plan --destination NEW [--backup NAME]`。它在没有 `current`、仅有 pending 操作，或已进入 recovery_required 时仍能输出观察结果。它仅读取有界的状态、意图、版本指针和可选备份 metadata，不启动 Docker/helper、不读备份密钥、不解密、不产生明文、不写源/目标文件、不建锁文件或目录。

- 目标必须是绝对、尚不存在且与源不重叠的目录；已有目录即使空也不接受，不把目录名当所有权证明。
- 只在既有安全 operation.lock 上尝试非阻塞排他 flock；不会绕过当前运行中的受信控制器。该锁不约束旧 CLI 或同权限外部写入者。
- 固定目录描述符读取普通、单链接、当前用户所有且私有的文件；拒绝符号链接、FIFO、不安全权限、过大内容、重复 JSON 字段与格式错误。
- 读取结束复查目录、文件和缺失路径的身份及元数据；检测到替换、改写或新出现的路径就失败，外部改动保留，不尝试“修复”现场。
- 输出仅含固定枚举、合法版本、观察指纹及未完成前置项，不输出状态中的邮箱、任意字段或原始异常数据。备份只观察 metadata，`authenticated=false`、`payloadsChecked=false`，不冒称 HMAC、签名、密文或卷内容通过。
- 所有输出都是 `status=execution-blocked`、`executable=false`。退出 0 只表示产生了观察结果；指纹不是签名、授权、租约或外部写入者排空证明。未来执行端必须重新验证，不能仅比较该指纹后执行。

## 切片 2：独占保留新文件系统目标（本 PR）

基于 main4756b6dc（含#108/#114/#117）在独立分支接续。`recovery-reserve` 必须收到明确的新目标和之前的观察指纹，重新观察并核对后才创建目录；观察哈希不升级为备份认证或执行授权。通过 no-follow 目录描述符遍历全链，逐层检查root/执行用户属主与禁止共享写权限，目标之外root-owned sticky祖先可例外，直接父目录仍须归执行用户所有且无组/其他用户写权限，创建能力仍由操作系统权限判断。

先在目标父目录中原子创建随机私有暂存及控制目录，生成reservation ID和项目名提案，以独占方式写reservation记录、空锁及pending意图；目录0700/文件0600，文件和目录fsync后重新核对整条路径身份、文件元数据及精确清单。随后用Ubuntu renameat2(RENAME_NOREPLACE)或macOS开发平台renameatx_np(RENAME_EXCL)不可覆盖公布完整目标，再fsync父目录并复核；接口/文件系统不支持就失败，绝不回退普通rename。首记录前失败不暴露可被普通安装采纳的空目标，失败暂存保留，其保留命名空间及内部路径由普通安装入口明确拒绝（含首记录前空目录）；重试可另建暂存而不接管旧残留。既有目标、重复/并发、改写、路径替换、fsync失败都不接管或删除现场，原实例及marker保持不变。成功只代表`reserved-not-authorized/executable=false`；不调用Docker/helper、不读密钥、不认证/解密备份、不创建运行资源。

针对原保留草稿，新增失败回归证明0775/0777或外来属主的更外层祖先会被私有直接父目录掩盖，再补逐层检查。原有用例覆盖竞争单赢家、指纹过期、目标替换、最终flush期间记录改写、写入失败保留、CLI与源字节不变。下一执行入口必须重新认证来源和目标/资源归属，不能把本记录或一次目录快照当作永久隔离租约。

本切片仍不完成下列第1项的Docker资源归属，也不解除任何执行门禁。

## 后续切片：在执行端接通前必须逐项证明

1. **独立新目标所有权**：原子保留新目录，生成新的不可猜测 operation/installation 身份和随机项目名；容器、网络、数据库和卷仅可引用本次明确创建的资源。拒绝固定 `survey-production` 复用、外部卷、未知挂载和身份漂移，不影响原实例或第三方资源。
2. **真实来源与认证**：重验 backup HMAC、文件库存/哈希、源 manifest/镜像、安全能力、数据库 schema 和严格版本范围。所选备份及可信执行内容在使用时重新绑定；本切片的 metadata 观察不能替代这些步骤。
3. **helper 与明文 containment**：选取经过审查的最小隔离机制，给每个 helper 有界执行、失败传播及整个 cohort 的终止/核验；证明超时、SIGKILL、进程树遗留和控制器崩溃不能留下未处置明文。不得把 TemporaryDirectory finally、父进程退出或 Docker stop attempted 当作证明；不重引入 #102 已拒绝的特权实验或未审 cgroup 工作流。
4. **恢复副本先隔离**：只向全新数据库/卷导入，先处理全部平台人员 grant/session 失效和归因审计，再启动副本应用；保持源数据、原授权与原标记不变。新副本不抢占原实例域名、端口、网络或写入路径。
5. **清理归属与证据**：清理只针对本次明确拥有的资源，容器库存含 stopped/created；各类资源独立尝试、有界确认，查询失败或未知结果保持 pending/failed。不要为了清理删除原备份、原标记或用户资源；不能用缺少证据当作无残留。
6. **验收和接线**：先隔离故障注入及真实双库/卷数据，再现有 GitHub CI，最后经独立审查后才由 Release 负责人考虑四平台原生接线。原生 gate、restore、失败安装和卸载的现有阻断在证据闭合之前一律保留。

原地恢复/重开原实例不是本阶段的替代捷径；它还要求对所有原实例写入者的独立隔离/排空、备份后数据处理和旧控制器约束作出可核验设计。本计划不将“受信宿主机”扩大为能防御同权限进程或 Docker/宿主管理员接管的声明。

## 验证和交付

首批失败测试验证入口不存在；随后补读取竞态、不安全路径/权限/锁、无 current、备份 metadata 非认证、CLI 和真实运行时清单测试。保留既有命令集合精确断言，显式增加新命令，不删除断言。测试仅用临时合成目录，无真实密钥或数据库。本 PR 不证明执行恢复、清理、原生、正式身份或生产准出通过。精确 head、实际运行和剩余项同步到 PR 与 #33，#33 保持验收中。

本地最终回归：控制器130项、Release/工作流167项通过；不重复跑本地完整产品/双库套件，托管 CI 以实际提交为准。

## 2026-11-11：方案 B 的既有范围核对与最小清理边界（待实现）

依据已确认生产设计的单机目标/非高可用范围、单机实施计划的 `surveyctl` 唯一运维入口，以及 #119 的 Owner 选择 B。本候选只需先证明单一生产栈的受控安装、升级、备份、诊断、卸载；没有证据表明已承诺同一 Docker daemon 上任意旧工具并发隔离。保留固定 `survey-production` 以兼容前代 helper 可作为本阶段最小方案，但**不能将现场排他性当作假定**。平台多租户/引擎实例管理不是多个 Compose 安装并发的承诺。

1. 新安装先获得 daemon 级原子归属、持久创建意图与私有身份记录；跨 target 争用仅一个成功，未知/既有对象不认领。固定名非运行容器是待验证的最小 claim 候选，不启动服务、不带挂载/网络/端口/特权；镜像必须来自既有可信 manifest 且明确可用。绑定 daemon、安装随机 ID、规范 target、claim 容器 ID；超时/响应丢失保持未知及 pending，不猜测成功或清空后重试。
2. 创建前/删除前对容器（含 stopped/created）、网络及成员、卷及挂载作 closed inventory，绑定实际创建记录。容器/网络使用实际 ID；命名卷不能虚构不可变 ID，须用名称、安装标记、driver、创建及挂载事实交叉核对，并承认同名重建限制。外来/额外/漂移对象、查询失败一律拒绝变更；不将未知对象交给 `--remove-orphans` 顺手删除。
3. 删除后有界核验：非零、未知、查询失败或残留不能写 uninstalled。普通卸载保留数据和卷集合，其归属记录不能释放后被其他 target 误用；purge 才要求受管卷和数据清除，并保留原精确确认。claim 最后处理，未知结果保留 pending 和现场。
4. 本轮 controller/helper/Docker exec 必须纳入可观测 cohort；未知旧工具/writer/exec 不放行。backup 的部分 pause、dump/加密失败、unpause/health/up 失败或未知，以及控制器终止都要故障注入；不能用服务名回退、签名资产、零退出或一次性 host 代替对象身份/收束证明。失败安装 pending 仍拒绝清理；只能记录失败并保留，不记成功清理。
5. 未来 stable 恢复仍按已定要求使用新项目名、新卷和新目标，保留全部六项证明；本固定项目范围不得沿用到恢复副本。若实际证明旧 helper 无法安全纳入 cohort，才需就跨版本 helper 执行契约或候选兼容范围提出具体选择，不能预先以范围不清停止所有验证。

### B 归属协议的组件证据（2026-11-11，尚未接入控制器）

独立 `fix/wave6-runtime-ownership` 分支新增协议、私有 ledger 与 Docker 适配器；37 项定向测试覆盖先持久化 pending、跨目标争用、未知创建结果不认领、未知 cohort/查询失败拒绝删除、前后身份漂移、残留与 claim 最后删除。私有记录拒绝不安全权限、符号/硬链接、FIFO、重复字段、超大内容及观察期间改写。非作者只读审查未确认组件阻断，不能等同运行验收。

显式入口 `platform/deploy/production/tests/integration/runtime_ownership_docker.py --image REPO@sha256:DIGEST` 使用已经存在且无 image-declared VOLUME 的镜像，仅创建不启动的随机 `survey-owner-fixture-*` 容器及一个空 local 卷、一个不连接容器的 internal bridge，无挂载/端口、不拉镜像；不操作实际固定生产命名空间。开发机 Docker Desktop client/server 29.9.3（Linux arm64 daemon）上使用本地 Alpine digest `294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6`：真实 daemon 单赢家、普通卸载保留同一卷及 claim、随后 purge 精确删除容器/网络/卷/claim 和最终不存在均通过，exit 0。`--fail-after-create` 故意失败保留 exit 1，测试夹具自身清理另外 verified，不冒称产品失败安装清理通过。首轮因 macOS `/var` 符号链接被正确拒绝；只修正新建夹具 root 的规范路径，不放宽生产 no-follow 检查。

独立复审另发现夹具未知创建结果可能误报清理成功：create 已成功但回复丢失时 `owned` 尚无 ID。两个回归先红后绿；最终独立查询随机 namespace 的全部容器（含停止态）、网络、卷，残留/任一查询失败/格式异常皆报 failed-or-unknown，未知对象只观察、不认领删除，且某类失败仍核对其他类。非作者增量复审确认该误报路径闭合；真实故意失败是在登记之后注入，并非真实丢回复或宿主崩溃实验。

此入口的 unknown cohort 是显式测试注入，**没有证明真实 helper/宿主进程收束**；镜像仅为本地测试材料，不是候选供应链证据。不证明生产服务/有数据卷生命周期、控制器接入、已签前代 helper 或四平台验收。现有两个控制器保守卸载失败回归仍保留，不能以组件绿灯替代。所有 restore/native/证据上传/清理门禁不变，Release 仍由 Claude 负责。

下一运行接入包必须包括：由已验签 Compose/manifest 产生闭合 contract；创建前归属与实际创建回执；controller/helper 的受控启动链与 Docker exec 身份/结束回执；删除前后核验。PGID、一次 ps 或当前 ExecIDs 为空均不能证明任意旧直接工具未来不会并发；单栈唯一入口是受控启动链的信任边界，不是扫描所得证明。已签 rc19 backup helper 使用 Compose exec、部分 pause 后才标记恢复责任，并在 finally 以服务名 unpause/up --remove-orphans；这些实际调用也须被适配器约束或明确调整跨版本 helper 执行契约，不能静默修改旧包或宣称已有回执。

本节只是实现/测试范围，不是运行许可或已完成安全证明；四道原生/清理阻断保持，Release 仍由 Claude 负责。已保存的保守测试在 main4756b6dc 上有两项预期安全断言失败，失败安装保留及 restore 拒绝不改 state/intent 通过；尚无真实 Docker 清理验收。

组件PR另在现有 production-smoke workflow 增加独立 ownership-protocol job：GitHub hosted Ubuntu24.04，contents:read，无OIDC/registry登录/持久凭据，先跑定向回归，再按固定Alpine digest获取测试镜像，执行随机空资源正常旅程并上传单份固定JSON。新增成本为一个轻量节点及一次小镜像拉取，不构建产品或触发Release；真实运行结果以该PR精确head的CI为准。10分钟job终止/runner中断可能阻止finally及JSON完成，此时不得宣称清理已验证；宿主销毁不是产品清理证明。

### 后续运行接入的具体实施包（设计待验证，不是已启用能力）

1. **从创建起记录**：候选controller读取并校验staged manifest/Compose，产生有限服务、镜像ID、路径/挂载、网络/卷契约；先取得daemon claim，再持久化创建意图。网络/卷的创建归因不能靠事后同名认领。正常uninstall改为按ledger精确ID删除及核验；失败安装未知结果保留pending，不执行猜测清理。安装、升级、backup、doctor与uninstall共用实际安装锁及归属检查，不能只给uninstall补一次ps。
2. **旧helper的兼容证据**：公开源码commit46766fd4326c370e0a5eeaa8de50235b5abb4a16中的backup.py SHA256为ceaaa316403f5fcf47162d3e310aa906a78cf5ecaab39ef2012b94b4cb1db3b0（仅Git源码证据，不冒称实际bundle已重验）。`BackupManager`已有runner/cipher注入点；数据库dump、容器复制、pause/unpause/up通过runner，但OpenSSL直接subprocess，必须一并纳入受控启动。controller `_verify_backup`还调用已安装restore.py的verify；verify_only会解密并验格式，不启动数据库，仍有明文/子进程收束要求，不能因为CLI restore拒绝就跳过。
3. **最小候选方向**：先隔离验证受控launcher加载经过现有签名/manifest校验且明确兼容的旧helper原字节，通过内部runner限制其Docker操作；旧包不改、不重签。与“改用候选helper并显式声明旧state/格式兼容”相比，该方向保留旧格式实现，但须逐项证明注入覆盖全部实际调用；不支持的版本/调用拒绝，不能悄悄回退原CLI。若真实旧包API不兼容，再向Owner给出这两种精确执行契约的证据及差异，而非笼统重选单/多栈。
4. **真实receipt，不靠空集合**：Docker exec需绑定实际ExecID、容器ID及结束状态，未知创建/启动/投递结果保持pending；当前无ExecIDs不代表本轮已结束。受控Python/OpenSSL启动链需记录PID/starttime并验证整个允许cohort收束；PGID本身不是containment。Linux无新增特权的父死亡约束/受控child bootstrap仅为待验证机制，须真实SIGKILL、子进程失败、controller死亡及异常派生负例；不得仅凭扫描或PDEATHSIG名称宣称通过，也不引入#102已拒绝的特权实验。
5. **保持原安全职责**：部分pause、丢回复、dump/加密失败、unpause/health/up异常均需真实组件故障注入；未知writer/exec/资源不主动kill或删除。验证私有工作区及明文清理必须建立在已证明cohort结束之上，不能以TemporaryDirectory finally或宿主销毁替代。备份HMAC、源manifest、schema、人员floor、restore拒绝及六项stable证明不变。此包只在隔离代码/合成数据验证，不启动真实组织或发布流程。

### 非特权父死亡机制可行性（2026-11-11，非产品containment证明）

可重复入口：`python3 -B platform/deploy/production/tests/integration/helper_parent_death_probe.py --docker-fixture python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`。仅使用本地已有镜像，随机自建容器，uid65534、cap-drop ALL/no-new-privileges、只读rootfs、无网络/端口/主机挂载，有限PID/CPU/内存及tmpfs；拒绝隐式VOLUME，按本轮捕获ID/标签/镜像/隔离参数核验清理，不拉镜像或改变主机权限。没有production/Release/凭据操作。

Docker29.9.3/Linux arm64实际exit0，固定JSON确认：(1)受控controller→helper→实际exec的OpenSSL链在测试controller遭SIGKILL后均不再存活，使用PID/starttime观察；OpenSSL仅用固定合成零密钥将/dev/zero处理到/dev/null，不是产品加密设置或真实数据。(2)父进程在bootstrap设置约束前已死亡，子进程校验父身份后拒绝继续。(3)故意绕过bootstrap、另起session的未包裹子进程确实存活，这是负控制，用于证明不能只约束直接helper；随后仅终止自己已记录的测试进程。(4)进程结束与容器删除后不存在分别核验通过。

最终重跑将宿主读取的源码SHA与容器内SOURCE字节的SHA相互核对，运行前/后及容器结果均为`1619e4e65915c5ba4a8f351a7511a559dad0aa79421a94b2786284a8af22f38a`，exit0；脱敏记录在本机`/tmp/survey-parent-bound-proof-fys84h2t/proof.json`。同时在启动前和清理时实际核对capDrop/capAdd、SecurityOpt、唯一tmpfs/无端口或设备、PID/内存/CPU上限以及原身份/私有PID模式/无bind/只读/uid；并非仅相信命令参数。5项纯Mock/编译回归包括真实换行、合法镜像名及空白拒绝；新增缺失/错误源码绑定与10种隔离属性漂移先红后绿，漂移必须在start前拒绝。此处不验证任意现存容器：内部worker入口只供本宿主受控启动链使用，不能凭uid或/.dockerenv替代宿主的隔离核验。

[Linux手册](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html)说明此设置在fork子进程中清除、通常跨exec保留，父线程提前死亡还需另行检查；实验结果与此一致。因此该机制**不足以独立证明任意旧helper收束**：需要已审查的每一条实际子进程启动及Docker exec路径都纳入可信bootstrap/receipt，不能把PGID/父死亡约束名称或本实验exit0当完整证明。尚未接入controller/旧helper，也没有验证真实备份/解密明文、Docker exec生命周期、Ubuntu四平台或崩溃恢复；临时合成文件和进程实验不解除任何门禁。

组件增量复核另确认非purge mutation 的后验条件缺口：effect成功后原卷消失/可观察指纹改变仍可写ready。8个operation×丢失/替换负例先红；修改为每个原卷名称/指纹必须在post snapshot原样保留，允许新增已声明卷，异常保持mutation-pending。37测试通过；这只证明可观察身份保留，不证明卷内容未被修改，也不解决同名重建指纹碰撞局限。

### 接入前置：匿名明文工作区（实现未接控制器）

`owned_memory.py`的生产backend仅允许Linux memfd及内核写封印，明确拒绝磁盘回退；私有0600、无目录链接、父进程FD默认close-on-exec。默认总量256MiB、32个逻辑文件，有界参数只供内部受控调用，不承诺任意大小备份。父进程写入前检查总容量；子进程导出提供输入封写及输出剩余预算，实际runner必须执行RLIMIT并验证子进程结束。导出或文件仍占用时不能报关闭成功，FD被替换不能关闭外来文件。匿名文件/opaque child argument拒绝普通字符串/文件路径转换，旧subprocess不能悄悄把对象repr当明文磁盘文件名；必须由受控runner解析授权FD。

14项定向回归通过（开发机portable匿名FD注入仅验证逻辑，不冒称memfd/seal证明）。真实入口：`python3 -B platform/deploy/production/tests/integration/owned_memory_native.py --image python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`。Docker29.9.3/Linux arm64、随机65534用户容器、无网络/主机挂载/端口、cap-drop ALL及只读rootfs/有限tmpfs，未拉镜像。最终模块SHA`46ce9e30cd42fc68a6964b8dd1254af256ba3ca7336c07c81b60724dd67e9154`、probe SHA`bede3eed5ced3de43650873da498002435fc093b6f89abb5bdd421c9fa7dbd19`，运行前后/容器内均匹配；内核封印拒绝写、RLIMIT拒绝超量、原FD身份消失、无命名工作区文件及测试容器清理均通过，exit0。非作者只读复核无确认新增阻断，未代跑实测。

该lease不启动/终止子进程，不能仅凭关闭父FD证明其他进程无副本；必须继续与实际helper/crypto runner及Docker exec回执组合验证。当前不改控制器、旧包、备份格式或Release，未打包/未接原生门禁，不将本内核实验标为明文生命周期、B清理或stable六项证明通过。

### 受控helper接入顺序与兼容契约（开发中）

候选自带的受控helper代码与经过校验的源安装state/manifest分开绑定，旧发行包不改、不重签。相较在旧模块里替换全局Path/tempfile语义，采用显式依赖注入：候选cipher支持受控runner及opaque FD参数，后续workspace/runner由可信控制器构造；不能从请求体或环境标记声称安全。未建立候选代码身份、源manifest、子进程回执和实际拓扑前，不能启用该路径，也不回退旧CLI。现有备份magic/AES-CBC/PBKDF2/HMAC及源manifest/schema/floor不变，跨版本兼容必须独立测试。源码对账还发现rc19 backup与当前版本的writer列表相差engine-relay；受控路径必须从已验证的源Compose选择完整writer集合，不能盲用候选常量暂停不存在的服务或漏暂停真实writer。

首个注入补丁仅修改候选backup.AuthenticatedOpenSSLCipher的runner/参数桥，未选择新的运行入口：3项先红后绿的合成回归验证匿名参数交接、原HMAC字节封装、认证失败不启动runner、失败仍传播且清除自身cipher临时文件；既有backup/restore 43项通过。该回归的fake runner不证明真实OpenSSL或子进程收束，后续需真实FD继承/父死亡约束及受控binary执行、容量限制与结束回执。

后续实施包保持单一写入者：①主线程受控launcher先持久化pending，再启动受父死亡约束的child，记录PID/starttime之后才释放启动管道；已结束/未知结果分别记录，不能从空进程集合猜成功。②OpenSSL只接收受控输入/输出/密钥FD，不将密钥值放argv或日志；限制文件大小及运行时间，失败先确认已知child结束再释放lease。③Docker exec创建/启动/结束绑定实际ExecID、containerID和固定profile；失联保留pending，不靠CLI零退出。④controller在创建起记录实际资源，已知失败才允许受控精确清理并验证后置条件；unknown/pending仍拒绝，不手动删marker。⑤最后才改运行接线和打包清单，并与Claude协调；Release/恢复/原生门禁不在本窗口解除。

### 受控 OpenSSL 子进程切片（未接产品控制器）

`owned_children.py`核对Linux主线程PID/starttime及父死亡/no-new-privileges约束；私有child receipt先pending、再记录实际PID/starttime、持久化released后才允许stdin释放。`owned_crypto.py`只提供固定encrypt/decrypt profile，拒绝自定义OpenSSL环境；实际bootstrap核对FD/二进制身份、输入封印、文件预算和时间上限，再以FD执行固定OpenSSL。未知终止或结束观察失败不能变成KnownCryptoFailure，保留released，不声称可释放明文lease。实际controller还必须持有这些lease并完成受控错误接线，当前没有启用。

新增回归先复现“退出0但仍活着/无法查询”误分类，修复后7项executor测试通过；child journal11项、匿名workspace14项、receipt名称38项、cipher桥5项和旧backup/restore43项通过。真实Linux实验还复现首次加密0、解密1：OpenSSL fd:读取会消耗共享密钥FD偏移。bootstrap在每次exec前将密钥offset归零后，同一密钥的真实往返退出码0/0。没有改密钥内容、HMAC、加密格式或旧包。

真实入口为`tests/integration/owned_crypto_native.py --image python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`。只用本地固定镜像和合成数据，非特权65534、无网络/端口/host挂载、只读rootfs、有限tmpfs/PID/CPU/内存；独立核对自己创建的容器身份及隔离属性。最新实测真实往返、截断密文拒绝、超时child已结束、tmp顶层没有额外命名文件、测试容器删除均通过，exit0。各模块运行前后及容器内SHA一致；crypto模块649dbf55…、bootstrap627a983f…；该实验不验证备份HMAC、更不替代产品清理或四平台准出。`/tmp`顶层检查不证明进程无其他FD副本。非作者复核发现外层仅核bool类型会接受false的P2，现增加独立全部为true及严格0/0退出检查，并补失败证据负例；不能靠子进程exit0兜底。

并行状态复查：远端main9b3b4e080dff合入#127，仅协作提示/看板及其测试，与本切片无文件重叠；#125保持Draft及原head9f8c7c7b，不把WIP分支组件绿灯回填为其controller已交付。当前未打包新运行入口，Docker exec、实际backup/verify helper、失败安装和卸载后置核验尚未闭环，方案B/恢复/native/上传/稳定版门禁全部保持。Release仍由Claude负责。

### Cipher适配器连接原认证封装（未启用）

`owned_cipher_adapter.py`将现有AuthenticatedOpenSSLCipher封装连接实际FD executor：捕获一次内部提供的合成/受信key bytes并立即封写，HMAC与OpenSSL均读取同一份；不允许命名明文、外来workspace/key、根目录外密文或任意命令。未知child与未预期executor异常持续持有导出context，阻断读写、关闭及复用，不用finally自动释放lease。只允许已确认结束的失败释放，外层helper必须保持对象/intent，尚未接线。没有放宽旧HMAC或增加磁盘回退。

非作者复核确认两个P2均先红后修复：另一uid可写非sticky祖先及操作中root替换；密文FIFO阻塞。适配器现核对完整祖先，持固定私有目录FD，所有密文open/stat/unlink均相对dir_fd、NOFOLLOW/NONBLOCK及普通单链接/权限核验，xb独占创建，仅删除本次记录dev/ino的文件，拒绝未知碰撞；权限通过FD fchmod，不追随路径。backup.py仅新增默认行为不变的_secure_output hook。对象close前核对FD，未知child时拒绝close；构造失败关闭目录FD。

7项适配器测试（合成executor）含原HMAC字节、篡改拒绝不启动child、未知持有/复用拒绝、立即封key、外来参数/路径拒绝、祖先及中途替换拒绝、FIFO拒绝；owned总40项、原backup/restore43项通过。真实native入口还在同一受限容器内执行现有HMAC封装的实际OpenSSL往返及篡改拒绝，无child新增，所有receipt completed，容器删除单独确认。源码hash前/后/容器内绑定；不替代完整备份/数据库/归档验证、helper父死亡集成、Docker exec或安装/卸载证明。未产生新PR/候选/Release，#125冻结待Owner决定，WIP仅作后续集成基础。

### 匿名 verify-only 调用链（未启用controller）

`owned_backup_verify.ControlledVerifier`固定cipher/workspace/backup位置及封写key，继承原manifest/HMAC/source release/version/schema/personnel floor与归档验证，显式拒绝restore，没有CLI或legacy fallback。`restore.py`仅将tarfile读取改为fileobj，支持匿名文件，现有格式和恶意成员拒绝不变。未知child异常继续上抛并持lease，不归类为普通解密失败。非作者限定增量复核未确认阻断。

2项新增合成回归先红后绿，含完整payload验证不创建磁盘明文、错backup及已通过认证的路径穿越归档拒绝；owned总42项、旧backup/restore43项通过。真实受限Linux入口还以实际OpenSSL加密/解密全部9份合成payload，经完整verify_only检查，18项child receipt全部completed，容器清理verified；9个模块运行前/后/容器内SHA一致。合成PGDMP/SQL头仅证明既有格式验证，**不代表真实数据库可恢复**。最终verifier SHA b0ab7dba…、adapter9a9886f7…、restore5c16412e…；控制器、可信候选/源安装绑定、Docker exec及失败安装/卸载仍未闭环，门禁不变。

### 实际 Engine ExecID 与输出完成协议（接入中）

`owned_docker_exec.py`固定本地unix socket/API1.47，拒绝外部DOCKER_HOST/context；仅静态预检/PG/MariaDB dump profiles，容器内timeout组为5或120秒，HTTP请求另有超时及有界查询次数，**不是端到端128秒硬上限**；不将实际口令放请求命令或日志。私有receipt先create-pending、实际ExecID持久化created、再released后才start；精确核对daemon/ExecID/containerID/Running/严格整数ExitCode。未启动与已确认结束的失败区别于未知结果；未知/查询失败保留released和调用者的lease，不能从空ExecIDs或CLI退出猜结束。旧receipt不能换输出后重用。Docker API错误body和stderr不记录，stdout按匿名FD预算及multiplex帧有界接收。

非作者复核发现并已复现：完整帧边界断流后exec最终0会误收缺尾dump。修复为本次随机nonce与payload SHA256的固定尾记录：容器内固定bash pipefail管道把同一输出经tee到原stdout和sha256sum，只在生产者/tee/hash均成功后输出尾记录；不落命名明文或把dump装入shell变量。接收端剥离/严格验证nonce/hash/尾记录，缺失/伪造/截断不能因后续exit0转绿。nonce只是本次流边界元数据，不是访问凭据或来源认证；源容器仍需真实安装归属及冻结writer约束。首次shell FD3时序错误已用固定合成echo最小复现，改为先exec复制FD再命令替换后真实通过。

9项定向测试通过，覆盖持久化先后、未知启动/结束、wrong ID/container/bool exit、非零与重复journal、帧截断/配额，以及完整帧EOF→running→exit0、分片/错nonce/hash/缺尾。真实入口`tests/integration/owned_exec_native.py --image python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`：本地已有镜像、随机自建非特权无网络/挂载/端口容器，实际预检ExecID结束0、缺pg_dump程序127、外来CID拒绝未start、容器清理均true；源码前/后SHA一致（模块bb12289b…）。guard明确为fixture注入，**不证明真实helper进程cohort、业务数据库dump或全局收束**。协议不是备份可恢复性证明，全部产品门禁仍保留。

依据：[Engine API1.47](https://docs.docker.com/reference/api/engine/version/v1.47/)、[Moby exec生命周期](https://github.com/moby/moby/blob/v28.5.2/daemon/exec.go)、[精确ExecInspect实现](https://github.com/moby/moby/blob/v28.5.2/daemon/inspect.go)。后者在从未运行时ExitCode仍未设置，不能把Running=false单独作为完成。非升级raw stream无总长度，故必须有应用层完成证明。

### 控制器清理接线与 Caddy 兼容镜像验证（2026-11-11，仍未解除门禁）

WIP 在真实 operation.lock 下连接 RuntimeController：卸载在 intent 前验证源配置、资源归属与 cohort；删除后逐对象核验，再清理已核对 inode 的 host 数据，最后释放 claim。未知查询、未知执行和残留不能记 uninstalled；旧安装无创建记录不自动认领。新增 `_owned_start_install` 仅将成功 up 后的健康/TLS失败视为已知失败；未知 up 不做这种推断。**默认 install 尚未调用此入口**，受控 hash/cohort、helper 启动、代码打包及实际业务拓扑仍需整合；旧 helper 在 owned target 上保守拒绝。

入口 `tests/integration/runtime_controller_native.py` 使用随机项目、固定本地 Python digest 和合成单服务有卷 Compose，以真实 SurveyManager/RuntimeController（无 runtime/cohort stub）验证：普通卸载保留同一数据卷且可回读、外来附件在 intent 前拒绝、purge 后资源与 claim 均不存在；模拟 DELETE 返回204但未删除时拒绝并保留 pending；成功 up 后 readiness 失败的精确清理保留数据和失败 marker。三场景均通过且独立 fixture cleanup verified，9个源码文件前后 SHA 相同。159项控制器、56项runtime、56项owned定向测试通过。证据仅为合成源码组件，不是签名候选、业务/数据库安装、从零 install、四平台或恢复证明。先前输出丢失的运行未记为通过；只读确认其随机 namespace 无残留后，新运行将 JSON 留在 `/tmp/survey-controller-cleanup-evidence-20261111.json`。

[官方 Caddy 修复](https://github.com/caddyserver/caddy-docker/pull/478) 指向 file capability effective bit 兼容问题。实际拉取并核验 `caddy:2.11.9-alpine`：索引 `sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee`；linux/arm64 child `sha256:9ee998103a2bc0d4392bbe9738b4069da1562d6920b3cd8088cfd098a8270120`；linux/amd64 child `sha256:fc9cbc100585871d18d4e451cca7021785c90a7006087427ae5ceb3e4efe6424`。**本轮真实执行仅 ARM64**：镜像标签及二进制均为 v2.11.9，getcap 为 `cap_net_bind_service=p`，非root65534、CapDrop ALL、no-new-privileges、readonly、network none 下 version 退出0；实际 stdin bcrypt helper 格式/结束回执/容器删除均通过，源码前后SHA相同。没有 CapAdd、没有剥除属性、没有改产品 CADDY_IMAGE；AMD64只核对registry元数据，尚未执行。hash JSON为 `/tmp/survey-caddy-2119-hash-evidence.json`，不含口令或hash。guard仍为明确fixture注入，不是完整进程cohort证明。交由Claude评估Release镜像固定及现有供应链回归，不能凭本实验直接换生产镜像。

#125 仍冻结原Draft；当前main已到22535aa9（#128管理端device-limit，无本切片文件重叠）。恢复/native/上传/stable门禁保持；B安全前提未全部完成，不触发Release、标签或生产操作。

后续 helper 接线增量：CohortProbe 仅接受严格 terminal caddy-hash receipt（同daemon/install/actor、固定profile/digest格式、整数退出码）并要求原CID实际不存在；停止但仍可重启的容器不算收束，pending 即使容器消失仍拒绝。测试先红后绿，已知failed+removed仅证明结束而非成功。RuntimeController.ensure_admin_hash 在 source/claim/cohort 复核后，使用实际ControllerProcessGuard、manifest固定CADDY_IMAGE及独立runtime-launch receipt调用真实helper；未知异常不写hash或runtime投影。私有输入/缓存文件使用NOFOLLOW/NONBLOCK、有界单链接普通文件、权限及读前后身份检查；缓存只验证安全性/格式，**不证明它与当前口令匹配**，不能把本切片当口令轮换机制。新增bridge mock单测验证未知传播、不落hash、固定绑定、合法输出/缓存、坏缓存及FIFO拒绝；runtime总59项通过。两个增量均经限定非作者只读复核未确认阻断，但Linux guard/helper真实组合、默认install操作标记及完整业务路径仍待验证，未启用默认入口。

### 默认入口接线与 Linux 质量验证接续（历史“未接入”说明不再代表本轮代码）

本轮 WIP 已将默认 install、backup、verify 和 doctor 接到 owned runtime：从创建起的 claim 先于 installing state；hash/start 使用受控入口，不使用 remove-orphans。backup/doctor 在控制器内执行，只有固定 profiles 的 Docker ExecID 和受控 OpenSSL 子进程；所有明文/密钥副本在有界匿名 workspace，256MiB/32文件预算无磁盘回退。保留旧 HMAC、源manifest、版本/schema/personnel floor、SQL/tar校验及512MiB磁盘预检。已知配置错误在 intent 前拒绝；未知 child/exec/RPC 保持引用和 pending，不允许同对象再启动新数据操作。已知库完整性失败仍映射5，未知执行映射4，消息固定，不透出原异常串。旧无创建记录安装不认领，restore 入口仍无条件拒绝。

独立复核确认并先红修复支持代码先 import 后校验的P1。现在17个执行文件必须与已验证 staged bundle 的 SHA/属主权限/加载位置匹配；全部 stdlib-only admission 完成后，按固定依赖顺序 compile 已捕获字节，不查找 PYTHONPATH 替身或消费未验pyc。另以实际 timestamp-valid 恶意pyc复现普通loader会执行副作用，child伴随 guard 改为直接编译受信源码。没有声称抵抗已接管的Python启动环境、同权限恶意进程或Docker/宿主管理员。

修复 spawn 早于 parent-death guard 即继承数据FD的窗口：child只继承私有 AF_UNIX SOCK_SEQPACKET；建立实际父死亡/NNP保护并核对peer PID/UID后READY；parent在durable released之后通过SCM_RIGHTS交付4个FD。接收端重映射实际FD，保留原身份/封印/预算及key offset检查。真实受限Linux/arm64加密fixture八项全部true、退出0、0/0往返、独立容器删除verified，9模块前后/容器内SHA一致；新增实际 `/proc/<child>/fd` 检查确认READY但未交付时不含明文/密钥。证据 `/tmp/survey-owned-crypto-bootstrap-fds-20261111.json`。这不覆盖所有父死时序或完整业务cohort。Caddy固定2.11.9的AMD64 child另经Docker Desktop/ARM64上的明确仿真，原ALL-drop边界真实version/getcap=p、stdin hash/结束/删除通过；不是AMD64原生准出，产品镜像配置未改。

真实Docker29.9.3的独立无数据fixture确认内建healthcheck会注册ExecIDs（2个，容器删除verified），与[Moby health实现](https://github.com/moby/moby/blob/v28.5.2/daemon/health.go)一致。adapter仅对有效忙快照做有界只读重查，最终仍要求实际空闲，不采用或标记任何未知ID终态；私有pending receipt独立阻断，paused/坏元数据/查询异常不提升。最多50次、重查窗口5秒，单次原查询仍可30秒，**不是总耗时5秒硬上限**。空快照不证明过去未知ID、后代或未来启动已结束；Root/Docker管理员边界不变。已知本轮尚未启动的唯一ExecID只避免等待自身，调用者仍严格核对。相关原强断言保留，63项runtime、76项owned定向测试通过；控制器168项通过。

新增 `tests/integration/owned_maintenance_native.py` 将实际Linux guard/Caddy hash、PG16.15/MariaDB10.11.19 dump、完整匿名verify、七份tar根文件精确内容、保留卷/purge及doctor拒绝/保持pending接到现有production-smoke的ownership质量job。只拉固定工具/DB镜像，权限仍contents read，只上传白名单JSON，不上传目录/日志/密钥/dump。该入口使用合成manifest/初始化SQL和Python占位服务，明确 defaultInstall/business/restore/release 均未取得资格；doctor仅TLS=false的拒绝场景。证据校验器拒绝bool schema、1代替true、缺项/空报告及误标资格。本机辅助cp/GET比较未确认非空源，不能计作内容布局通过；已停止重复本地探针，由该Linux入口严格校验真实卷内容。**新入口尚待精确head的GitHub CI，不能写已通过**。

Claude接续点：`release/build_release.py:RUNTIME_FILES` 尚需在独立审查后包含 `surveyctl.OWNED_RUNTIME_FILES` 的13个新增模块；当前不会绕过缺模块的failclosed检查，也未修改Release工作流/打包文件。已有source-production smoke的旧入口不证明这些默认入口。后续仍需完整默认安装/正向业务doctor、故障矩阵、实际包及B原生验收；owned升级接线也未据此完成。#125仍冻结原Draft，恢复/native/上传/stable门禁保持，未触发标签、GHCR、Release或生产操作。

Owner明确授权后已创建Draft PR #129（初始head `2f35d871c9d4691be3de79ab93205d223f5deecd`）。首次Linux维护job `43102867372` 两场景均在initialize被人员能力绑定校验拒绝（surveyctl.py:1195），独立Docker清理均verified；尚未进入数据库备份。根因为合成夹具替换images后仍沿用旧平台digest的能力声明。定向回归复现同一拒绝后，仅修正合成manifest声明绑定测试镜像；错误digest负例继续拒绝，产品校验未改，四项证据测试通过。修复后的在线真实维护结果仍待验证，不能把本次旧失败或其他smoke通过当作本路径通过。

第二轮 `43103485356` 已通过真实AMD64 Caddy hash，但在backup失败；UncertainMaintenance位于writer恢复检查，fixtureCleanup为unknown-or-refused，不能称已清理。隔离真实Docker明确返回暂停容器Status=paused/Running=true/Paused=true，恢复后running且该临时容器清理verified。以同状态模型复现本轮代码在pause观察及finally恢复前拒绝本轮已登记CID。仅对本次paused集合中的CID、真实Paused=true且原归属/指纹/exec检查全满足时允许paused状态；外部暂停仍拒绝。11项定向维护测试通过，未知/丢失RPC的保守语义不变；修复后完整链仍须在线验证，不断言这是唯一后续缺陷。

Owner明确批准一轮限定超时修复后，核对原head `bb6a89089c4ca35fbace1a45caa1a6f5099b633c` 未变。其维护证据run43104179073确认真实hash、backupVerified、archiveContentsVerified及restore拒绝均true，失败推进至uninstall的Docker响应等待超时；清理unknown-or-refused，旧证据没有请求方法，不能证明当时一定是stop。另完整CI43104179354有独立FD复用单测EBADF失败（test_owned_memory.py:147），本轮不扩大修复范围，也不跳过或声称全绿。

定向模型先红证明9秒stop响应被原5秒传输预算拒绝。修复仅为无body的固定POST `/containers/<64hex>/stop?t=20` 提供25秒响应等待，连接/发送保留5秒，其他请求保持原5秒；只接受204/304无body确认。该预算不是整次卸载硬时限，也不改变daemon的20秒grace。超时固定分类container-stop/docker-json，不保留路径、请求体或异常原文；原有unknown语义不变，无重试或CLI fallback。实际cleanup调用链的超预算负例证明只有一次stop、保留cleanup-pending及原资源、不发删除请求。10项runtime_docker、12项exec、4项维护证据回归通过。

真实本机Linux Docker组件实验（`/tmp/survey-pr129-stop-budget-20261111.json`）：随机自建非root、network-none、无端口/宿主挂载、只读、ALL-drop容器忽略TERM；一次stop实际20.08秒收到确认、精确退出检查通过，再删除并核验无残留，未重放未知mutation。不代表旧CI唯一根因、完整卸载、数据库生命周期或四平台通过。修复后精确head仍需GitHub CI；恢复与发布门禁保留，未触碰Claude Release。

Owner另明确批准FD身份安全限定修复。原head72c94843的真实维护/生产smoke43108925648三job均成功，artifact13895671965两场景逐项复核passed且Docker cleanup verified；仍为合成manifest/占位应用、非完整安装/正向doctor/四平台。完整CI43108925694原FD复用断言未成立及后续EBADF仍阻断；旧日志没有inode，不称已证明唯一ABA原因。

匿名workspace现在为每个文件保留独立、不可继承且不导出的私有duplicate，持续钉住原inode，防止公开FD关闭后原对象释放并被重用。原FD与reference均需匹配已记录身份/私有匿名条件；原FD被外来对象替换时不关闭外来FD，只释放仍可独立验证的本方reference，workspace永久failed，不能恢复成成功。reference自身未知则保留未知状态，不猜测close。正常close/unlink及分配/protection失败均测试两FD释放；无磁盘fallback。边界明确不防御任意同进程代码并发篡改FD表，尤其检查后再替换或同时替换两FD，不承诺此类竞态安全。

先红新增引用/释放测试，保留原replacement安全断言；18项便携workspace、82项owned组件测试通过。真实非root/ALL-drop/NNP/network-none/readonly Linux memfd入口扩为9项：identityPinnedAcrossReplacement、foreignReplacementRejected、noReferenceDescriptorLeaks等均true，exit0、源码前后SHA一致、临时容器cleanup verified，JSON `/tmp/survey-pr129-memfd-pin-20261111.json`。真实memfd路径验证原对象存活时外来同mode/uid匿名文件身份不同，不把它追溯当旧CI inode重用记录。入口已接既有ownership质量job，精确提交的Linux在线结果仍需核实，未扩大权限、未操作Release/候选/生产，全部资格门禁保留。

独立复核后，故障注入确认了部分退役缺口：public FD已关闭但anchor close失败时，unlink未封锁workspace；workspace.close还可能再次尝试同一anchor。先红覆盖unlink/close、close前报错及close后号码已被外来FD复用四种组合。现在close前记录anchor_close_attempted，任何退役close未知立即永久failed，保留FD/identity记录；不重试、不允许同名/新文件或export，不将未知状态改为成功。分配错误清理也保留部分引用，不能覆盖，只有完整已验证退役后的新分配可重置尝试标记。正常清理仍释放已验证本方FD，未知引用保留至进程退出，不能称无残留。

21项便携workspace、85项owned测试通过；真实Linux memfd新增uncertainRetirementBlocksReuse（实际close后dup2外来FD再注入错误），十项全true，源码前后SHA一致、容器清理verified，最终JSON `/tmp/survey-pr129-memfd-retirement-final-20261111.json`。独立故障夹具显式清理其自建FD，不是产品恢复流程，也未清除产品未知标记。上一head130a09ff的smoke43115963126已通过，新增故障语义仍需新head的CI及独立复核，不能继承旧绿色结果。按Owner最新指示，解除恢复、原生或发布门禁前必须先说明具体操作并取得同意；本轮全部保留，不合并、不发布，Release仍由Claude负责。

### 2026-11-12 受控升级接线（独立分支，待审/待在线验证）

以#129精确c59a441648b6ce2db3da4fde569b72b8f0dcb785为冻结基线，在`fix/wave6-owned-upgrade`独立推进，不改其head或Claude #130/Release。原代码已有upgrade枚举但控制器没有接线，还会使用旧Compose/rollback路径。新默认路径在intent/staging前以stdlib检查源归属文件存在及私有性，不自动认领rc19或其他旧安装，也不因不支持的旧安装请求就改写其状态。

**代码授权与数据授权分开。** 使用来自经验证目标包的控制器执行升级；先按目标staged bundle校验全部17个实际执行模块（原SHA/属主/权限/非pyc准入不变），再启用仅限锁定upgrade操作、不可由请求参数选择的内存执行版本上下文。上下文内旧数据仍按原runtime-contract、原manifest/env/overlay及claim/daemon校验，备份版本/schema/manifest SHA/HMAC及人员floor仍绑定源版本。上下文在成功和失败时都清除；普通backup/doctor不能借用。代码字节不同的源包按原规则会被当前执行器拒绝，但经目标包准入的升级执行器可操作经独立验证的旧资源；源manifest不被替换。包/镜像签名与来源信任沿用现有正式制品链，不从源码夹具伪造capability。

先做真实源版本受控备份及核验，再拉取固定目标镜像/建立目标合同。新旧持久网络与卷定义必须完全一致；本切片不实现持久拓扑迁移。原容器按旧合同/精确CID逐一停止、结束确认、非force且v=false删除，并核验原CID不存在；不删除网络/卷。任何未知响应不重放、不切源。全部旧容器退出且持久ID未变后，在mutation-pending内写入目标合同/overlay、以`up -d`启动（无remove-orphans），健康/TLS通过且前后网络/卷指纹相同才完成并切current。原claim/CID及其固定镜像保留，不重新认领；目标应用镜像可改变，但claim来源仍是原受信安装。合同与overlay的多文件切换不是原子事务；中断会留下operation intent/mutation-pending并拒绝继续，不能自动修复或假称回滚成功。

失败进入recovery_required，只有验证完成的备份名才记录为recoveryBackup；不调用旧Compose回退、不启动旧镜像、不恢复数据库。已知完整性错误保留原退出分类，未知执行固定RuntimeHealthError。可能存在备份后的写入或迁移，保留所有状态供受控恢复设计；恢复仍禁用。

定向测试先红覆盖：退役先于合同切换、未知停止不启动、持久拓扑改变及启动时网络替换不settle、无源码权限的旧安装在intent前拒绝、目标代码不匹配不得启动备份、目标代码上下文不得逃逸、失败不调用旧恢复。经真实staging元数据的测试证明字节不同的旧包身份仍被保留，并非用目标manifest重写旧备份身份。协议/管理器测试含显式fake daemon/host，不是原生验收。

实际退役边界再次校验目标代码和完整目标source contract，拒绝prepare之后的漂移；未经prepare或没有目标上下文的runtime实例不能直接升级。远程`upgrade_version`与本地manifest两种入口共享该锁定授权，旧安装在远程下载前也拒绝。174项控制器、73项runtime、85项owned定向测试通过；新真实组件升级的在线结果待CI，不将此前无升级步骤的绿色证据冒充当前组合。

现有Linux维护夹具扩展为真实PG/Maria容器同代码双版本切换：原容器ID全部消失、新ID绑定，claim/网络/九卷ID与指纹保留，再执行普通卸载与purge。host/DNS/TLS是显式模型，manifest是合成包，`upgradeBusinessDataVerified=false`；不证明业务答卷数据连续性、跨任意历史代码兼容、完整安装、正向doctor或候选准出。占位服务改为显式TERM退出以避免无意义的六次20秒等待；不响应TERM的真实20秒stop合同已另有独立实验和负例，产品stop预算不变。该路径复用现有quality job，完整在线证据尚待新head CI。新增运行模块数仍17，Claude #130打包无须再加名称；Release/候选/门禁解除仍由相应授权流程处理。

Draft #131初始head4ad004f97f1aea1dd132db30c798974795837d25的真实组件job43125523570已success；artifact13902846793逐项确认ownedUpgradeCompleted/persistentIDsKeptAcrossUpgrade/oldContainerIDsAbsentAfterUpgrade/newContainerIDsBound及原备份/归档/卸载/purge/helper结束字段均true，两场景Docker fixtureCleanup verified，源码SHA一致。资格限制字段仍false/模型标记true，不能提升候选资格。完整CI43125523585先报告既有RC最低来源版本测试失败：源归属前置检查覆盖原版本floor/preflight错误。只将归属检查移动到只读preflight之后、intent/staging之前；版本排序及原Release测试不变，该精确失败测试与六项升级定向回归通过。修复后head完整CI及组件需重新验证，不继承旧绿色证据。
