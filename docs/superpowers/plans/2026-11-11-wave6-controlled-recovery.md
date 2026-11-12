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

### 2026-11-12：静默 exec 的连接/执行预算分离（待审）

独立审查的5秒读超时已用真实私有Unix socket先红复现：服务先返回HTTP200，静默5.5秒后才发送合成一帧，旧实现读取首字节时socket.timeout；不访问Docker或生产、不含凭据。连接仍固定5秒，stream仅允许固定profile，读取预算为该profile命令秒数+5秒；开始读取后每次单次buffer/recv前核对同一deadline并设置剩余socket时间，不因滴流重新起算。命令自身timeout、帧/字节预算、nonce和终止ExecInspect校验保留。不将此称整个CLI/HTTP头解析/清理的硬总时限。

先红后绿真实5.5秒静默正例；模型负例逐一验证所有profile预算、非法profile在连接前拒绝、滴流不能延长deadline且只发出一次start。超时仍是unknown并保留released回执/lease，不能重放不确定操作，也未提高任何成功判据。该切片独立于exec GC与重装合同问题；不操作Release、生产或候选，恢复/原生/发布门禁保持，具体解除前须Owner明确同意。

同轮独立GC修订已合入此待审分支（仍以#129 c59a4416为stacked base）：Docker29.9.3真实原容器保持running，旧回执在自然GC后536.49秒变unknown；新版测试521.26秒观察到同样GC，v2精确终止观察仍有效、v1及pending继续拒绝，已终止ExecID的detached restart明确409，临时容器清理verified且源码SHA前后一致。attached start先返回流HTTP200才处理backend拒绝，不能把200当重启证明；真实负测使用detached路径，不放宽断言。可重复入口：tests/integration/owned_exec_native.py --image <本地固定Python镜像digest> --wait-for-gc，最多等待660秒；默认入口只跑快速ExecID/终止观察，不宣称每次CI都观察自然GC。

新schema2回执只由受控writer在精确terminal ExecInspect及daemon/actor/output身份复核之后写入；实时仍查ExecInspect，只有精确GET /exec/<id>/json的404才使用绑定daemon/install/container/exec/exit的既有终止观察。v1没有该证据，GC后不自动认证、迁移、删标记或重放；timeout/500/身份冲突/运行中状态仍unknown。该回执是现有私有目录/单写入者边界内的持久观察，不是密码学证明，不抵御同权限/宿主/Docker管理员接管，不替代全局cohort与实际资源归属检查。fixture的owner是注入模型，不称完整安装或真实controller隔离证明；未取得业务连续性/四平台/恢复/Release资格。重装合同顺序问题仍独立待解决，旧#129绿色CI不能作其安全通过依据。

### 2026-11-12：卸载后重装合同绑定（独立待审）

先红复现两项：install在prepare前生成带旧overlay的目标Compose；普通卸载/数据purge后prepare尚未将目标version及当前generation写入合同，hash admission的实际_load检查拒绝。修订只让image pull使用已验证source-only Compose；任何helper/start仍在prepare之后，不能拿source-only路径启动服务。

prepare仍先验证旧合同、属主/daemon/claim/cohort。仅retained-data或removed可继续；先完整验证目标source，保留已核验claim image lineage，不把旧claim静默替换成新镜像。retained-data要求网络/卷合同完全一致，并在目标查询后重新核对旧归属；purge必须经既有exclusive acquire产生新installationId及retired history。然后绑定目标source、overlay和当前generation，才允许hash helper与install effect。不存在对旧无归属安装的自动认领。部分合同持久化失败仍由_load不一致拒绝，不回退旧overlay、不称已恢复；需要保留现场受控对账。

模型回归覆盖两条重装到ready、新容器归属、保留卷、purge新generation、活动状态/目标未验证/网络卷漂移/查询失败不改账本资源、purge目标无效不新建claim，以及第二份overlay写入失败后admission继续拒绝；另经实际SurveyManager.install入口核对source pull早于prepare而overlay不可提前加载。此为控制器/CLI定向证据，不是受信完整产品安装、真实双库重装或四平台验收。依赖#132/#129，Release/恢复/原生/发布门禁保持，任何具体解除前Owner另批准。

### 2026-11-12：经Owner批准的chunked缓冲接口与异常关闭限定修订

独立deadline复核后，真实本机Docker29.9.3 unix exec实测HTTP200且chunked=true；不能按Moby原始raw-hijack源码推断所有部署都没有HTTP chunk封装。拒绝chunked会破坏正常路径，该尝试未提交。现将HTTPResponse原有fp缓冲器包入共同deadline reader，不丢弃已缓冲字节；chunk header、body、trailer的read1/read/readline都逐次设置剩余socket时间并检查返回时间，迟到EOF不能成为成功。只支持无Transfer-Encoding或明确chunked，不接任意编码；连接与HTTP头解析仍不冒称整个CLI硬总时限。

Owner已明确同意对缺失flush及异常关闭进行一轮限定修复。flush委托原缓冲器；异常路径只尝试一次connection.close，关闭也失败时保留原失败/超时、不重试、不声称清理成功或释放unknown lease；正常读取后的close失败仍向外失败。先红复现真实HTTPResponse.close缺失flush掩盖deadline，以及immutable c3ddb886慢trailer模型未在预算内拒绝；修订后95项owned与69项runtime通过，包含实际HTTPResponse逻辑时钟trailer、flush故障仍关闭buffer并保留原超时、迟到EOF、close失败无重试、released无completionEvidence及私有Unix socket5.5秒静默正例。

真实Docker补验入口：owned_exec_native.py --image <本地固定Python镜像digest> --read-timeout，默认CI不自动运行该慢路径。本机Python3.9.6的socket.timeout不是TimeoutError，夹具只兼容这两个明确超时类型，不扩大到任意OSError。实际chunked正向通过；固定15秒sleep在10.002秒读取超时、GET确认仍running，再仅观察自然退出0，无重放；容器cleanup verified、源码SHA前后一致，JSON /tmp/survey-exec-chunked-timeout-verified-20261112.json。此超时段直接调用本夹具API，不是完整controller/lease/全局进程证明；writer保留unknown由独立状态机回归验证。真实慢trailer网络投递未演示，逻辑时钟负例与实际普通chunked/静默超时证据分别记录。旧GC实验未为此重跑或扩张资格；v1与256目录项/安全退休仍未解决，恢复/原生/发布门禁均保持。

### 2026-11-12：隔离终态回执收束原语（未接默认入口）

环境重连后核对独立fix/wave6-terminal-receipts基于#133 83e720cd且无未提交现场；先红新增失败测试，再实现ReceiptRetirement。只读plan始终executable=false；run重新采样，最多收束16个已重新证明结束的回执，整个目录256项硬限制不变。实际资源/claim验证及durable begin-operation是受信controller回调边界，不可由外部输入断言；当前无默认调用者、无新CLI命令。先验证整个cohort/owner/文件身份，再持久pending并复读，逐文件删除前后复核集合、lock/目录/文件身份与资源；父目录fsync后确认缺席，任何失败不清operation.intent、不重试未知操作，不触碰运行容器或数据卷。

v1仅在精确live ExecInspect终态或原不可变CID证实缺席时可成为候选；已GC且原CID仍存在的v1继续unknown。v2精确终态观察的GC兼容沿用#132，500/timeout不兼容。pending/未知回执与恢复标记从不收束。超过256的已有目录不自动救场；默认生命周期接线、开始前最坏容量预留、崩溃后受控处置及真实资源查询成本均未验证，故本原语不代表长期运维问题已全部闭合。

隔离模型测试覆盖v1/v2 GC差异、资源漂移、超容量零写入、intent缺失/写失败、symlink拒绝、unlink/fsync失败保留pending、收束期间回执变pending拒绝、16批上限及重复周期累计271个历史回执而活动目录可收束。仅临时文件/Fake API与资源回调，不是Docker/完整controller或候选证明。旧8项cohort回归随新测试复用；独立runtime套件73项通过。所有恢复/原生/发布门禁保持，不合并、不发布，Release由Claude负责；默认接线需下一切片及独立复审，不清除未知标记来换取成功。

独立复审后的容量/查询增量（仍未接线）：先红确认256项且无intent时旧原语会先写pending才拒绝；21回执原算法每项重扫远端，完整资源回调34次。现在写intent之前预留一项原子发布临时文件容量（即使已有intent也需替换临时槽），满256时零写入；255项临界模型验证临时文件峰值256。目录上限不增加，已经超限的目录仍不能自动处理。

完整终态/cohort/资源核验在begin前、begin后和batch后执行；中间已证明不可重新开始的终态见证仅在本锁内使用，逐项核对全部剩余文件身份/内容摘要、owner、锁、目录集合及pending身份，不再逐文件重复全量远端查询。这修订了上一段“逐项资源核验”的实现方式；若文件/集合改变立即停止，batch后的远端漂移则失败并保留pending，已删除的是先前严格核验终态的回执，不会据此重放或放行后续操作。可信同进程/独占controller边界不防御宿主或同UID恶意篡改。

新增先红后绿及批中pending/事后资源漂移负例后23项（含8项复用cohort）通过；73项runtime回归通过。21回执模型、资源回调也实际调用cohort时，删除16项为141次ExecInspect、141次缺席查询、3次资源回调（新断言上限192/192/3），不是实际Docker时延。仍有本地O(batch×目录项)核验成本，远端每次查询也有独立超时，未证明整个收束的墙钟预算；真实性能、默认接线与每种业务operation的最坏容量预留仍未验证，不宣布容量已闭合，不解除任何门禁。

身份一致性增量：先红确定性复现_local_check初次stat之后、load之前同内容原子替换会被接受并删除；未证明正常独占controller可自然触发，也不称生产攻击。现owner/receipt加载后均再比较原文件身份，unlink前再核对目录链与目标身份。两项故障注入分别在load内部及_local_check返回后换同内容新inode，均零unlink、保留pending。回执测试25项（含8既有）及runtime73项通过。

边界明确：最后stat与POSIX unlink之间仍不是原子比较删除，依赖既有可信独占写入者协议；不能保证抵御最终检查之后任意同进程/同UID/宿主管理员的并发路径替换。两个可检测窗口的模型通过不代表所有TOCTOU已消除。远端资源核验始终是批前/批后边界，不把批内本地见证复核写成逐项实时远端验证。默认入口保持未接线，真实性能及整体墙钟预算待验，所有门禁保持。

### 2026-11-12：手动备份容量接线（独立待审）

独立分支基于#134 c3353eea，未改其他待审head。默认owned备份在输出/密钥/空间等已有preflight通过之后、原begin/helper之前调用prepare_backup_capacity，使用真实runtime.preflight_cleanup回调和manager durable begin；固定预留2个DB exec+1个共享cipher journal，实际分配点每次扣减，耗尽时禁止新journal/exec/FD export。reserve先判定即使收束全部已核验终态也能容纳；不可能则零写入。只有旧记录均可实时核验结束时才按16批上限逐批收束，成功后留出3个永久回执、缺失intent及1个原子发布临时槽；已有满256目录仍拒绝。unknown v1不补证、不认领，不清操作/恢复标记。失败/中断留pending，重建manager下一操作在runtime前拒绝。

先红后绿容量分配与默认backup顺序/配额耗尽负例；27项回执（含8既有）、73项runtime、11项maintenance及default_backup4/capacity3/exhausted2定向测试通过。仅模型及实际CLI方法，不是真实进程SIGKILL/候选/双库全流程证明，交GitHub既有真实组件CI继续验证。共享内部备份helper调用同样保护，但不宣称#131升级组合已测试。默认doctor/verify/install/upgrade其他生命周期未因此接线：doctor遍历备份目录数量无固定界限，需动态预算，不能假定小常数；每种operation、总墙钟预算、所有崩溃窗口/真实候选连续性仍待。

旧v1 GC+原CID存在和已超256目录保持失败关闭，当前没有指定且获准操作的真实安装目标；后续只能先做只读盘点与精确处置提案，不能为了可用性运行旧controller、抬阈值、移走未知回执或停止/删除原资源。最终stat/unlink非原子、可信独占controller边界不变，所有恢复/原生/发布门禁保留。此为开发分支代码，不合并/发布/触碰生产，Release仍Claude负责。

#135预检成本复审增量：旧reserve会清空全部终态，非“只删够容量”；完整旧21回执真实本地API样本249 inspect/249缺席/9资源回调、0.944秒，不能与#134单批0.483秒混用。现容量足够时只核验、不删历史、不写新intent；不足时最多一批16，超出一批所能证明的需求在开始前拒绝。最多5次完整capture，并设置30秒协作式成功准入预算，捕获/本地检查前后核时；迟到资源结果不得放行，若已写pending则保留。opaque资源回调/阻塞文件系统调用不能由该预算强制中断，不称30秒硬墙钟deadline或整个backup时限。已知v1的主动清空策略不再适用：留存v1将来失去live证明仍按unknown拒绝，不承诺自动迁移。

先红两项：已有足够容量仍删历史、迟到回调仍成功；修订后30项回执测试、default_backup4与runtime73通过，新增pending后预算耗尽零删除。真实Docker29.9.3/linux/arm64新reserve样本：有空间21条留21、63 inspect/63缺席/1资源回调、0.167秒、intent保持不存在；252项压力样本删16留5、219/219/5、0.720秒、pending保持。JSON /tmp/survey-reserve-room-20261112.json 与 /tmp/survey-reserve-pressure-20261112.json，均捕获CID清理verified且源码SHA前后一致；owner/claim/进程reader仍模型，不是完整controller资源回调/真实backup端到端或SLA，单次无故障样本不能证明高负载表现。

升级组合新增边界已记录：共享backup helper的3配额不覆盖后续独立_owned_maintenance verify的新cipher journal，整体至少还需1槽；独立复审在252项非回执基线模型看到backup255、verify256及后续原子写峰值257。该组合在base已存在，不当作本PR新手动备份缺陷；本PR仍未整合#131或声明升级预算已证明。后续需跨阶段预留及验证，当前不解除原生/恢复/发布门禁。

升级组合预算复核增量：先红证明prepare_backup_capacity对upgrade/upgrade_version仍传reserve3，独立verify也无配额。现升级备份预留4条（2 DB exec+共享cipher journal+后续独立verify journal），备份实际分配限3；_verify_backup在自己的begin/解密前prepare_verify_capacity重新核验并预留1、实际分配限1，失败不启动解密。252目录项含249有效终态回执及完整intent模型，reserve4先腾空间，随后4次真正PrivateStore原子发布的临时峰值均<=256，后续ledger临时槽也有余量；下一backup预留通过。模型不是自然真实升级必现，#131完整组合仍未整合验证，先前“只非回执filler”限制不再适用。手动backup固定3语义不变。

协作预算措辞与检测增量：先红复现_local_check返回时已经超预算仍先unlink1条、资源回调迟到后仍多查一个回执。现capture在资源回调返回后和逐回执边界核时，local_check尾部及最终stat后/unlink前再核时，两个确定性已观察过期窗口零删除。不能因此承诺所有过期零删除：最终检查后时间仍可推进；第4/5次完整核验迟到时已有终态删除，新增回归确认最终拒绝且pending保留。opaque回调内仍不能强制中断，30秒非硬deadline。最终stat/unlink非原子、独占controller信任和批次远端核验边界不变。

本增量34项回执、73项runtime、11项maintenance、default_backup4及upgrade/verify配额和verify拒绝顺序定向测试通过；未删除/跳过安全断言。仅隔离模型和真实管理器方法，真实SIGKILL/受信候选/升级端到端性能仍待GitHub组件与后续候选证据。所有恢复/原生/发布门禁保持，未合并/发布/碰生产或Claude Release。

### 2026-11-12：doctor动态回执预算（独立待审）

环境恢复后确认只有3项先红测试、源码未改，再继续同一独立分支（base #135 cea3301308）。默认owned doctor在新begin/helper前准备容量：2个迁移exec+最多30个relay cron+1个close，即33，再加当次每个备份verify独立cipher journal。relay循环与预算共用doctor.PROBE_MAX_RELAY_EXECS=30，原执行上限未改变。实际journal/exec分配沿用强制配额，不因预留成功取消分配点检查。

备份root与成员使用非跟随stat身份，有界扫描最多256目录项，隐藏/非目录/链接不算备份；拒绝不安全属主/可写目录。固定root与备份清单身份在reserve后及每次verify前/扫描完成后复核，新增/替换/消失拒绝继续，不以静默截断清单制造预算。原语仍最多一批收束/5个完整capture及30秒协作成功预算，放不下即拒绝，不抬状态目录256上限。33+N超过可证明容量时零helper；没有硬总deadline/真实性能保证。

先红三项后修订，15项maintenance、6项doctor相关管理器测试、73项runtime通过，default_backup4也通过；大清单/224个journal需求、准入后新增清单、原不存在root变为存在、容量拒绝早于intent/helper等模型覆盖。首次无备份仅warning断言原样保留，夹具补上实际admission对象。仅模型与管理器方法，真实候选doctor/双库业务旅程/完整升级/真实崩溃未验证；交GitHub既有组件CI及非作者复审。

旧v1 GC+原CID存在和已有超限目录仍保守拒绝，不补证、不移走未知回执、不执行未授权现场清理。可信独占controller、非原子stat/unlink、协作预算可能有已完成删除及pending保持边界不变；所有恢复/原生/发布门禁保持，不合并/发布/碰Claude Release或生产。
### 2026-11-12 受控升级接线（独立分支，待审/待在线验证）

以#129精确c59a441648b6ce2db3da4fde569b72b8f0dcb785为冻结基线，在`fix/wave6-owned-upgrade`独立推进，不改其head或Claude #130/Release。原代码已有upgrade枚举但控制器没有接线，还会使用旧Compose/rollback路径。新默认路径在intent/staging前以stdlib检查源归属文件存在及私有性，不自动认领rc19或其他旧安装，也不因不支持的旧安装请求就改写其状态。

**代码授权与数据授权分开。** 使用来自经验证目标包的控制器执行升级；先按目标staged bundle校验全部17个实际执行模块（原SHA/属主/权限/非pyc准入不变），再启用仅限锁定upgrade操作、不可由请求参数选择的内存执行版本上下文。上下文内旧数据仍按原runtime-contract、原manifest/env/overlay及claim/daemon校验，备份版本/schema/manifest SHA/HMAC及人员floor仍绑定源版本。上下文在成功和失败时都清除；普通backup/doctor不能借用。代码字节不同的源包按原规则会被当前执行器拒绝，但经目标包准入的升级执行器可操作经独立验证的旧资源；源manifest不被替换。包/镜像签名与来源信任沿用现有正式制品链，不从源码夹具伪造capability。

先做真实源版本受控备份及核验，再拉取固定目标镜像/建立目标合同。新旧持久网络与卷定义必须完全一致；本切片不实现持久拓扑迁移。原容器按旧合同/精确CID逐一停止、结束确认、非force且v=false删除，并核验原CID不存在；不删除网络/卷。任何未知响应不重放、不切源。全部旧容器退出且持久ID未变后，在mutation-pending内写入目标合同/overlay、以`up -d`启动（无remove-orphans），健康/TLS通过且前后网络/卷指纹相同才完成并切current。原claim/CID及其固定镜像保留，不重新认领；目标应用镜像可改变，但claim来源仍是原受信安装。合同与overlay的多文件切换不是原子事务；中断会留下operation intent/mutation-pending并拒绝继续，不能自动修复或假称回滚成功。

失败进入recovery_required，只有验证完成的备份名才记录为recoveryBackup；不调用旧Compose回退、不启动旧镜像、不恢复数据库。已知完整性错误保留原退出分类，未知执行固定RuntimeHealthError。可能存在备份后的写入或迁移，保留所有状态供受控恢复设计；恢复仍禁用。

定向测试先红覆盖：退役先于合同切换、未知停止不启动、持久拓扑改变及启动时网络替换不settle、无源码权限的旧安装在intent前拒绝、目标代码不匹配不得启动备份、目标代码上下文不得逃逸、失败不调用旧恢复。经真实staging元数据的测试证明字节不同的旧包身份仍被保留，并非用目标manifest重写旧备份身份。协议/管理器测试含显式fake daemon/host，不是原生验收。

实际退役边界再次校验目标代码和完整目标source contract，拒绝prepare之后的漂移；未经prepare或没有目标上下文的runtime实例不能直接升级。远程`upgrade_version`与本地manifest两种入口共享该锁定授权，旧安装在远程下载前也拒绝。174项控制器、73项runtime、85项owned定向测试通过；新真实组件升级的在线结果待CI，不将此前无升级步骤的绿色证据冒充当前组合。

现有Linux维护夹具扩展为真实PG/Maria容器同代码双版本切换：原容器ID全部消失、新ID绑定，claim/网络/九卷ID与指纹保留，再执行普通卸载与purge。host/DNS/TLS是显式模型，manifest是合成包，`upgradeBusinessDataVerified=false`；不证明业务答卷数据连续性、跨任意历史代码兼容、完整安装、正向doctor或候选准出。占位服务改为显式TERM退出以避免无意义的六次20秒等待；不响应TERM的真实20秒stop合同已另有独立实验和负例，产品stop预算不变。该路径复用现有quality job，完整在线证据尚待新head CI。新增运行模块数仍17，Claude #130打包无须再加名称；Release/候选/门禁解除仍由相应授权流程处理。

Draft #131初始head4ad004f97f1aea1dd132db30c798974795837d25的真实组件job43125523570已success；artifact13902846793逐项确认ownedUpgradeCompleted/persistentIDsKeptAcrossUpgrade/oldContainerIDsAbsentAfterUpgrade/newContainerIDsBound及原备份/归档/卸载/purge/helper结束字段均true，两场景Docker fixtureCleanup verified，源码SHA一致。资格限制字段仍false/模型标记true，不能提升候选资格。完整CI43125523585先报告既有RC最低来源版本测试失败：源归属前置检查覆盖原版本floor/preflight错误。只将归属检查移动到只读preflight之后、intent/staging之前；版本排序及原Release测试不变，该精确失败测试与六项升级定向回归通过。修复后head完整CI及组件需重新验证，不继承旧绿色证据。

**网络证据范围更正：** 独立复审确认此前夹具全部network_mode=none，没有实际network；上述旧run的持久网络比较仅为空字典相等，撤回“真实网络ID保持已实测”的表述。九卷、claim、旧CID消失/新CID绑定仍有实际证据，网络代码保护仅有模型测试。已在#119及#131公开更正。先红新增拓扑及空/0/bool/缺失count不能通过的回归，夹具现在仅给一个无监听、无发布端口的占位进程接单独internal bridge，其余服务仍none；要求retainedNetworkCount为严格整数1，并且旧网络集合非空。5项证据/隔离边界测试通过；产品Compose、capability、Release配置不变。新的非空网络ID/指纹保持实测仍待新head GitHub CI，不把旧制品当作该证明。

### 2026-11-12：授权后的依赖整合

Owner明确同意依赖整合/合并、具体门禁解除、候选执行及发布。此次在独立`integrate/wave6-controlled-runtime`组合#136修订链、#131升级与Claude #130打包；原分支不改。仅计划文档追加冲突，保留两侧记录。相对已验证main22535aa9没有引入上述PR之外的新功能；整合态仍需精确head完整CI和非作者复核，不能沿用各自旧基线的绿色代替。

此提交没有解除恢复/native/发布门禁，没有创建标签或发布；授权不等于缺失证明已经通过。后续由Claude协调最终不可变候选及Release，避免重复执行。恢复六项证明、真实候选安装/正向doctor/升级业务连续性、四平台及人工读屏仍独立核验；旧rc19不可自动认领。可信独占controller、非原子stat/unlink、批次远端核验、协作预算和旧v1未知/超限目录保守拒绝的限制保持。

### 2026-11-12：恢复副本的显式资源命名空间（未接恢复执行）

基于main7cdd66c4独立参数化OwnershipProtocol、DockerOwnershipDriver及API适配器的内部project。默认仍survey-production；额外只接受survey-recovery-加32个小写hex，绝不从已有record推导或认领project。实例构造时固定project/claim名，恢复范围要求adapter明确匹配；record、claim、卷身份、资源发现/指纹及删除使用同一实例绑定，不用修改模块全局常量。原旧夹具的一项测试将全局项目设置移到实例构造前，所有合法/非法卷名断言保留；新增全局后来变化不能重定向已构造实例回归。

恢复合同目前仅支持空资源/数据卷层：卷和网络必须属于本project，卷必须local，网络必须internal bridge；volume mount只可引用本合同卷，host bind和外部命名资源拒绝。未来应用配置挂载须另有目标根准入设计，不在此切片扩大。来源project对象不会吸收到副本清单；未知create保留claim-pending，不重放或猜测清理。mutate操作仍不含restore，没有接CLI/恢复执行端或修改其拒绝。

首批7项因缺接口失败，追加无project adapter拒绝先红，再实现；10项新scope/原生夹具查询保守测试、45项ownership、12项API、14项controller和184项surveyctl通过。包含跨project record/卷/claim拒绝、邻居与停止容器清单、全局漂移、未知创建、外部卷/bind/public网络拒绝、volume返回畸形/某类查询失败仍核对三类，以及restore仍不可调用。

新增真实入口tests/integration/recovery_namespace_native.py --image alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6。只使用已有镜像、两个随机新私有目标，各一只永不启动且无挂载/端口的隔离claim、一只不挂载的空local卷和一个不接容器的internal bridge；不操作真实survey-production。通过同一daemon的真实API/只读投影和私有账本完成独立归属、删除第二组不改变第一组，再精确purge；未知结果保留private state，不删除或重试pending。

本机默认socket访问被沙盒拒绝后，经正常require_escalated授权只读检查固定镜像，再运行此范围的测试，无权限改写或socket绕行。Docker Desktop Linux arm64实际exit0，firstNamespaceUnchanged/firstNamespaceRemoved/secondNamespaceRemoved=true、fixtureCleanup=verified，源码前后SHA一致；JSON /tmp/survey-recovery-namespace-20261112.json。claimsNeverStarted=true、cohortProbeIsModel=true、businessDataCovered/restoreAuthorized/releaseEligible=false，不能说已证明任意helper/writer收束、真正源业务不变或新副本恢复。生产smoke现有ownership-protocol轻量job追加该入口和白名单JSON，不新增权限/凭据/产品镜像构建；精确PR的amd64在线结果待CI，不把本机arm64等同Ubuntu四平台。

仍须将#139目标预留、#140来源/目标兼容性与此命名空间在未来执行器绑定，再证明新副本真实数据库/卷导入、人员授权失效及归因审计、故障/明文/资源清理和业务核对。Release由Claude #138负责，所有restore/native/上传/发布门禁保持；每项具体解除前说明操作证据风险并取得明确同意。

### 2026-11-12：真实双库新副本的合成数据组件（stack #141，未接产品恢复）

新增recovery_database_native.py，基于#141显式namespace协议在两个随机私有目标各创建PG/Maria及明确新卷，共四个DB容器/四卷和两只从不启动的claim；无host bind、发布端口、容器网络或业务app，固定本地PG/Maria/Alpine镜像，无脚本拉镜像或特权/cap-add。角色/数据库使用一次性network-none夹具的local trust/空口令初始化，不模拟生产认证；512MiB/1CPU/128PID及NNP约束沿用测试边界。

真实PG应用当前58份迁移（实际role platform_owner），创建虚构组织、人员grant/session和fixture_recovery_response；Maria创建同名合成记录表。实际pg_dump/custom及mariadb-dump输出仅在host Python内存，经stdin导入全新目标DB；不写具名host dump、不调用产品restore或旧_restore_payload。源端在两份dump后追加记录，验证源两行而副本仅快照一行；副本内直接调用现有人员失效SQL，正向先确认目标旧grant/session有效，再验证失效、关联trace审计存在且源grant/session不变。实际应用从未启动，不称真实问卷/业务恢复。导出顺序仅适用于无并发业务writer的本夹具，不证明双库事务快照。

先红4项缺实现，后追加pending持久化和非空目标拒绝2项缺实现回归，再实现。同步fixture exec若timeout/非零/畸形输出即永久unknown，不重放、不清理；每个私有目标在创建Docker资源前以O_EXCL/no-follow写0600并fsync的operation.intent，整个实验保持pending，检测同字节替换与权限/身份漂移。非空/不独立目标在import前拒绝并保留状态。仅全部已知结束、意图仍匹配、精确资源删除及三类全无残留后，才清理自建夹具目录；未知时保留目录/标记并输出保留路径，不认领或删除真实安装标记。意图/cleanup规则是测试保护，不是产品exec回执或恢复授权。

首轮真实双库复制通过后发现仅进程内unknown标记不足，未将首轮当最终结果；增加上述两项安全前置、审计trace关联和私有root文件清单核对后重新运行。最终6项新夹具回归及10项namespace回归通过；Docker Desktop Linux arm64实际exit0，targetWasFresh、资源独立、PG/Maria合成行、源post-snapshot保留、目标人员失效/源授权不变、审计关联及pending贯穿等字段true，fixtureCleanup=verified、privateStateRetained=false，源码及58迁移库存hash前后一致。最终JSON /tmp/survey-new-copy-database-final-20261112.json，不用首轮旧脚本哈希冒称最终源码验证。

强制负资格字段sourceAuthenticationVerified/targetSignatureVerified/controlledExecReceiptsCovered/restoreAuthorized/releaseEligible/crossDatabaseTransactionVerified/productionAuthenticationVerified=false。dumpTransportHostMemoryOnly=true不是memfd/加密备份链，也不声称Python heap已擦除；具名host dump只核对本夹具私有root及当前固定命令路径，数据库数据本就写入自建卷。cohortProbeIsModel=true，同步CLI结果不能证明任意宿主writer/未知exec已收束。已有#142真实OpenSSL/memfd和旧PG owner/audit故障证据仍是不同组件，未被本测试自动组合成全链路。

现有production-smoke ownership-protocol在既有固定DB镜像拉取/维护组件之后追加此实验，仅上传白名单JSON；预计额外四个短期DB容器和四卷、最多2GiB DB内存配置，无新权限/凭据/候选镜像构建。#141该job既有时长约2分19秒，仍保留10分钟总门；超时/中断无证据不能宣称清理。线上amd64精确证据待CI。Release由Claude #138负责，恢复/原生/上传/发布门禁均未解除。
### 2026-11-12：匿名来源验证后的新目标兼容性（内部接口，未接恢复）

独立于#139目标预留守卫、基于main7cdd66c4，在现有ControlledVerifier增加verify_target_compatibility。原verify_only检查的是原安装当前版本的兼容范围，不能直接证明备份适用于另一个目标；新接口先要求明确的目标manifest小写SHA256，并用现有load_manifest完整schema校验及内容绑定，再调用原verify_only进行HMAC、文件/源manifest/镜像身份、版本/floor与全部匿名payload格式校验。随后对目标minimumSourceVersion、目标版本上界、schema及source现存人员floor作兼容判断；读取前后重新加载固定hash目标，拒绝长验证期间改写。

输出sourcePayloadsVerified/targetMetadataCompatible只描述本次内部检查，不是恢复能力票据；targetSignatureVerified、targetConfigurationVerified、restoreAuthorized固定false。来源/目标的签名、控制器代码/operation/资源归属、目标有效配置、生命周期及未知child的cipher/workspace责任仍在未来调用者；接口没有CLI/restore调用者，也没有启动数据库或应用、失效人员授权或复制明文到新目标。现有restore拒绝不变，未改Release或门禁。原owned_backup_verify已属17模块包，此次只是新增未接调用方法，不新增模块声明或候选能力。

先失败测试证实接口不存在，再实现；schema负例夹具最初缺backupSchema，经补齐真实schema字段后保留不兼容断言。10项新测试覆盖缺失/错hash、版本上下界及RC minimum、schema、坏HMAC早于decrypt、人员floor降级、目标中途改写及资格false；连同2项继承的原匿名验证测试通过，另外独立运行原2项和43项backup/restore回归通过。测试使用实际HMAC/清单/格式/FD适配器，但cipher是显式CopyExecutor模型、workspace是portable backend；不称真实OpenSSL、Linux memfd、新副本DB或恢复准出通过，Linux结果待精确CI。

下一步仍需新的Docker项目/资源从创建起绑定、源备份/目标包在使用时复验、真实双库新副本及人员session/grant失效和归因审计、故障注入/收束/精确清理。不得把metadata兼容或原HMAC通过当成未来恢复执行授权，所有具体解除前说明证据、范围及风险并取得明确同意。

### 2026-11-12：来源/目标兼容性的真实Linux crypto验证（stack #140，执行门未动）

先核对原check_restore_owner_contract组件，人员grant/session失效、审计失败回滚、旧会话拒绝等已有真实PG证据，本轮不重复实现。优先补#140当前CopyExecutor/portableFD的证据缺口：扩展现有owned_crypto_native.py隔离夹具，带入同源surveyctl/schema/fixture manifest，调用实际verify_target_compatibility。每个case单独匿名workspace、受控cipher和child journal，避免多个验证复用既有解密输出或累积超过child预算；旧8项crypto/错误/期限/无继承测试全部保留，新增7项证据必需字段及缺失/false/1拒绝。

先红证明原证据不要求新接口，修改后2项证据测试、12项兼容性回归通过。既有固定Python镜像下真实Docker Desktop Linux arm64测试exit0：正向targetMetadataCompatible及sourcePayloadsVerified=true，同时targetSignatureVerified/targetConfigurationVerified/restoreAuthorized=false；错误目标hash为0 child，版本/floor拒绝均在完整匿名payload校验后且所有child已结束；坏control-HMAC为0 child；无具名解密payload、restore调用仍拒绝。原加密往返、截断/坏MAC、deadline、FD不继承和noNamedPlaintext仍true，最终containerCleanup=verified，12份同源文件的before/inside/after SHA相同，JSON /tmp/survey-recovery-source-crypto-20261112.json。

边界仍是actual OpenSSL/Linux memfd处理合成PGDMP magic、Maria注释头及小tar；未导入数据库、未启动副本应用、未认证目标签名或新目标配置，不称完整SQL安全性/数据库恢复/Ubuntu四平台。沿用原非root65534、cap-drop ALL/NNP/network-none/readonly、16MiB tmpfs、无host bind/端口及CPU/内存/PID限制，无新增权限/镜像拉取或workflow入口。现有ownership-protocol job已调用此夹具，精确PR的amd64证据待CI；父PR #140原head不改。restore/native/上传/发布门禁不变，Release仍Claude负责。
### 2026-11-12：新目标预留的使用时校验（内部切片，未接执行端）

基于已合并main7cdd66c4独立实现`recovery_reservation.RecoveryReservation`，不改Claude #138的Release区域。先建立失败回归，再实现只读上下文：通过no-follow目录FD检查祖先/直接父目录及0700目标/control；读取0600、单链接、普通文件的精确三文件清单，JSON有界且重复字段/布尔伪整数/授权字段变化拒绝。reservation ID、随机项目提案、source observation、记录中的目标与control dev/inode均与调用绑定；source observation只是绑定，不是备份认证。持有原operation.lock非阻塞排他锁，不创建、修复或清理任何对象；FD不可继承。读取前后和正常退出复核文件身份/摘要及目录路径，额外项即拒绝，枚举只保留有限预期项。首次失败永久封锁该上下文，即使外部随后移除异常项也不能重新取得成功。

先红的目录读取期间替换和失败后复用两项均修复；14项目标测试以及184项原surveyctl回归本地通过，包含真实recovery-reserve产物接续、symlink/hardlink/FIFO、权限、拷贝/错绑定、锁冲突、同字节替换、原地改写与外来目标保留。仅本机真实文件系统/故障注入，不冒称Linux各平台或宿主崩溃证明；Linux结果由精确PR的现有CI核实。

模块目前未加入OWNED_RUNTIME_FILES、没有CLI或restore调用者、不进候选执行包。所有输出固定executable=false/backupAuthenticated=false/runtimeResourcesCreated=false，只代表观察到的预留未变。锁约束可信独占controller，不防御同UID恶意写入者/宿主管理员，也没有原子的最后检查到未来使用间隔或永久租约；后续执行器必须继续持有FD并在使用前复核。

后续依赖顺序不变：新的Docker项目/卷/网络从创建起归属且未知拒绝；受控匿名验证器重验备份HMAC、清单、manifest/版本/schema/floor；新副本双库/卷导入及人员grant/session先失效并审计；数据验证后才考虑独立切换。故障注入、明文/子进程收束和精确资源清理仍需逐项证明。此切片不完成上述后续步骤，restore/native/上传/发布门禁保持；每项具体解除前说明证据范围风险并取得明确同意。

人工读屏事实已重核：#41存在Owner于2026-11-10记录的VoiceOver passed与发布批准，均绑定rc19，而非“完全未完成”；后续候选须核对差异覆盖及批准范围，不自动继承rc19记录。

### 2026-11-12：恢复前置实际组合与下一执行包

在独立integrate/wave6-recovery-preconditions组合#1434313d6c4（含#141a2ff8d）、#142d916（含#140f5f7）和#1392f82。两个merge只在本计划追加处冲突，保留双方全部段落；14份实现/测试/workflow blob与对应原head逐一相同，全部五heads为祖先。7个release/原生门禁、surveyctl/restore/owned_docker_exec入口blob与main7cdd66c4不变。91项有范围的同进程组合回归通过；本地只检查组合，不重复旧head全量。记录/tmp/survey-recovery-composition-20261112.json；真实组件组合及整轮CI仍待新整合head，不沿用旧分支结果。

这证明组件共存准备，不代表五项已接成同一个数据流。#143非作者已核对AMD64 artifact13936201630的真实双库合成数据/58迁移/源增量/授权失效/清理，ZIP摘要与源码hash匹配；#142非作者核对AMD64 artifact13935060098的15项真实FD/crypto检查。没有独立Sigstore签名验证声明，也不提升为候选或真实业务恢复。

下一执行包须先测试、实现和独立复核，以下尚未实现或启用：

1. **受控stdin写入链**：现有OwnedDockerExec为输出型profile，没有DB import输入路径。只能消费认证后、受同一workspace/operation绑定并只读封印的匿名FD；限定目标owned CID/service、有限PG/Maria导入及人员失效命令，不接受任意SQL/shell/request-body命令或具名host明文。新增ExecID/输入长度摘要/期限/输出及结束回执，部分输入、丢回复、超时、GC未知均保持输入/输出lease和pending，不重放。不能借现有backup/doctor profile冒充恢复写入。
2. **原目标与源认证接线**：#139持锁FD、reservation/project/target身份须与#141新scope、daemon/claim/新卷互相绑定；#140本次payload验证不能当持久capability，使用时重验源/目标包签名、manifest、HMAC、schema/版本和有效人员floor。目标有效配置还未被metadata接口证明。
3. **副本先隔离**：实际双库和全部卷只写新scope；导入失败保留隔离副本，Maria DDL不能假称全库事务回滚。应用启动前完成grant/session失效、归因审计和数据校验，不能沿用源实例域名/端口/外发连接或原管理员有效授权。
4. **故障闭环与准出**：补部分导入、进程/控制器SIGKILL、审计失败、意图/资源漂移、未知exec/明文与清理的实际故障注入；source保留及已知/未知分别记录。之后才是真实受信候选、正式身份/业务连续性和四平台验收。具体恢复/原生/发布门禁解除前，给出精确操作/候选/证据与风险并取得用户明确同意。

本整合不做上述执行接线、合并main、打tag、发布或真实部署；Release由Claude #138负责，所有门禁保持。下一轮优先收束整合CI与非作者审查，不再用重复空资源或合成样本代替执行链。

### 2026-11-12：受控 stdin / ExecID 传输切片（保持恢复门关闭）

Stack #144 的独立分支新增固定 `stdin-proof` 消费器，只运行有界 sha256sum，不新增 SQL/import profile、restore operation 或 CLI 入口。生产路径要求真实 Linux 写入/增缩/封印四种 memfd seals、私有匿名且不可继承的输入；私有 dup 保持 inode 存活，逐块及末尾复核原 FD/anchor 的身份、长度、封印。调用方提供的输入 SHA 只是长度/内容绑定，不代替备份来源认证。输入不得与输出同 inode，最多 1GiB；每次只创建/启动一个 ExecID，不重放。

新 v3 回执在 create 之前记录输入 size/hash/delivered=false；只有固定消费者摘要、原输出 nonce/footer、精确 CID/ExecID 终态、daemon/actor/output 复核与输入私有引用关闭成功后，才能记 delivered=true/completed。旧 v1/v2 输出回执契约不变，cohort 对 v3 严格字段、整数/布尔类型、摘要和终态校验。部分发送、超时或结束未知仍留 released/pending，不自动清理或重新提交；保留输入/输出工作区由上层负责，当前没有产品恢复调用者。

LocalDockerAPI 使用 ExecStart 101/tcp 升级、原 HTTP 缓冲、同一个35秒协作预算、分块 sendall 和半关闭 EOF；200/chunked 不作为双向输入成功。sha256sum 只在读完输入后输出，故此固定 consumer 可顺序发送再读；不能把它当作需要并行 stderr/backpressure 的 PG/Maria 输入协议。原只读/输出型 stream 的 chunked 支持保持。

先红后绿：原无接口/回执拒绝被11项定向测试覆盖，原 Exec14/cohort8/memory21回归通过。额外覆盖伪封印拒绝、错长度/hash/profile、外来FD替换不关闭、部分发送未知不重放、错误消费者摘要/footer、错误CID终态、严格 v3 schema、101缓冲/EOF、200拒绝与send超时关闭。模型中的 seal 查询使用便携测试 seam，不声称内核证据。

本机 Docker Desktop Linux arm64 真实 transport-only 实验成功发送大于socket缓冲的合成数据，经101、输入摘要、输出footer和实际ExecInspect终态核对后清理精确临时容器；/tmp/survey-exec-input-transport-20261112.json 明确 kernelSealsVerified=false、controlledReceiptVerified=false。新增 Linux native 夹具将真实 AnonymousWorkspace/memfd 与 OwnedDockerExec/private receipt 连成同一正向组件，接入现有 production-smoke ownership job；源码hash、有限JSON、unknown保留资源，且清理结果单列。该组合尚待精确head CI，不是数据库导入或业务恢复。

#144 的五workflow已全部success（CI43205373012、smoke43205372769、relay43205372889及元数据）；独立组合复核无新增确认阻断。新预留守卫仍未进入受校验 runtime 打包清单，产品接线前必须处理。新的受控 stdin 切片仍须独立审查；所有恢复/原生/上传/发布门、Claude #138 Release 工作流均未改动。下一步是在此传输基础上设计限定数据库consumer的双向背压、来源/目标使用时认证与故障注入，不能用hash消费组件替代实际双库恢复。

#145非作者发现并确定性复现双故障：部分stdin发送已抛UncertainExec，finally关闭私有输入引用又抛OSError，会覆盖原未知分类。补红测验证原异常类型/原发送cause、released回执、input未确认、输入仍可读及只关闭一次，再修为保留原UncertainExec；没有原未知异常的关闭失败也保守转UncertainExec，不重试可能复用的FD。12项新输入回归与14项原Exec回归通过。后续head需自己的CI，旧4397428a的绿色smoke/实际memfd产物不能代替此修订验证；门禁保持。

### 2026-11-12：数据库导入前的背压与副本 cohort 条件

独立stack #145ce6 的输入消费准备，未新增数据库profile/SQL导入/restore入口。确定性Unix socketpair模型先让消费者在读输入前写出超socket缓冲的stdout及受限stderr；原顺序sendall在5秒测试预算内超时，证据/tmp/survey-input-sequential-red-20261112.log。新增同线程selector事件循环，在同一协作deadline内增量收发、有限frame解析及stderr丢弃，保留HTTP预读缓冲、EOF半关闭和输入身份复核。无后台线程在异常返回后访问caller FD；连接/返回关闭异常仍按#145未知结果规则处理。35秒是该固定profile的协作流预算，不是包括哈希、Docker元数据、终态观察和关闭在内的完整操作硬上限。

原12项输入测试的断言保留，仅将旧sendall socket模型改为明确readiness模型；新增真实socketpair背压、fragment/frame限额及截断负例。真实Docker Desktop arm64 transport-only已验证大输入与预先512KiB stdout/48KiB stderr、nonce/digest和精确终态及容器清理，JSON /tmp/survey-exec-duplex-transport-20261112.json，kernelSealsVerified/controlledReceiptVerified=false。新native夹具另外注入故意不提供合法footer的真实消费者，要求背压链结束后是failed/exit0/input-unconfirmed，不可误当合法stdin-proof；这项Linux组合待精确CI，不拿本机模型冒充。

另发现CohortProbe此前仍硬编码生产project，不能验证#141的随机恢复namespace。先红测，再增加构造时固定的project（默认生产不变），只接受现有project_name契约；不同副本/生产record、非法namespace均拒绝，之后全局变化不能重定向既有probe。此改变仅让已有锁/回执校验可显式绑定scope，不证明完整资源/宿主进程归属，不解除任何门禁。

数据库写入前仍有明确接线条件：#139初始预留守卫只接受精确三文件，创建runtime-owner/exec回执后不能直接继续inspect，需受控且持锁的预留→运行归属handoff；不得简单放宽未知文件。来源必须先通过ControlledVerifier现有HMAC/全inventory/匿名payload校验，再在使用时封印/绑定输入；target metadata检查仍不证明签名或有效配置，不能用caller SHA代替认证。随后有限PG/Maria consumer必须绑定同一已证明的新scope/CID/service/operation、记录输入ack与精确终态，再独立核对提交/数据及目标人员失效。双库部分成功/超时不重放，隔离失败副本并仅清理归属及cohort已证结束的资源。当前未实现该高层产品链，restore/native/上传/发布保持关闭，Claude Release不改。

补充新native夹具的Mac预检：以明确注入的portable FD/seal/platform测试模型跑真实Docker正向及故意缺footer背压消费者，portableFDActualExec=true、backpressureConsumerEndedRejected=true、cleanup=verified，无残留fixture；/tmp/survey-duplex-full-fixture-20261112.json。kernelSealsVerified=false，不能把此结果称Linux内核验证；现有CI将对原始未注入的Linux代码执行同一正负流程。全程未新增DBprofile或恢复执行调用。

### 2026-11-12：预留到运行记录的持锁文件系统交接（仍非执行许可）

为处理#139精确三文件与未来owner/exec记录的接线冲突，独立stack #146 新增内部`RecoveryReservation.handoff(receipts=...)`及`ReservationHandoff`。先按原守卫检查完全空预留，再以no-follow/O_EXCL创建0600、不可继承、fsync的`recovery.handoff.json`，内容固定pending-not-authorized/executable=false并绑定reservation/source observation与最多16份预先登记的child/exec文件名。marker创建期间只能存在原三文件+marker，哪怕提前出现的文件名在计划内也拒绝、不认领。成功后原目录/文件/同一个flock FD的所有权直接转给handoff，不解锁、不重开；原guard失效且退出不关闭已转移FD。

交接后的根仍只允许.surveyctl，控制目录只允许原锚点、immutable marker、runtime-owner及预登记文件；锚点/目录身份和内容持续复查，额外项永久poison。可变runtime记录仅通过既有PrivateStore核对普通文件/单链接/私有权限/严格有界JSON，不校验资源或执行语义，输出runtimeRecordsQualified=false、backupAuthenticated/targetSignatureVerified/restoreAuthorized/executable均false。原reservation记录只是历史预留，不能用其runtimeResourcesCreated=false推断交接后的真实资源不存在。

先红后绿与真实本地文件系统验证共10项：外层guard退出后锁仍被持有，handoff关闭后才可重新取锁；不变的原intent与marker保留；计划参数/早到record/短写/fsync中断、同字节marker替换、原intent替换、symlink/hardlink/public/FIFO、失败后移除异常仍拒绝、__enter__失败释放私有FD、close结果未知不重试。交接失败也永久poison原guard；实现没有删除marker/未知对象或修复旧现场。结合原guard、输入、背压/cohort/retirement等同进程96项回归通过，/tmp/survey-handoff-combination-20261112.log。没有实际SIGKILL/宿主崩溃证明，可信独占controller之外同UID对抗/最终check-to-use不是原子fence。

新模块及原reservation guard仍未进入OWNED_RUNTIME_FILES，未被产品restore/CLI调用；没有Docker变更、DB导入、受信目标签名配置或候选资格。该交接只提供未来控制器保留原锁/锚点并容纳有限运行记录的文件系统层；必须继续组合实际OwnershipProtocol/CohortProbe与源/目标认证，不能拿此返回值授权执行或清理。restore/native/上传/发布门保持，Release仍Claude单一写入者。后续具体解除须向用户给证据/范围/风险并获同意。

#147增量先红后绿：非作者自然with双故障复现为handoff fsync未知结果被外层guard关闭ReservationError覆盖。新增测试在旧源失败，修为保留同一原始UncertainExec及其fsync cause；原FD单次关闭后立即复用的外来FD仍存活，marker保留且发布不重放。另自查并复现runtime JSON读取后替换immutable marker/追加未知文件会漏过最终观察，补末尾锚点及无JSON回调的清单复核，两类注入拒绝。新增后共12项handoff/同进程98回归通过，修订仅guard/handoff、对应测试和本计划。原head绿色不能代替新head CI，仍非原子check-to-use或同UID对抗保证，未发生产品恢复或解除门禁。

### 2026-11-12：认证输入到实际数据库消费者下一包（在独立分支准备，尚未实现/验收）

基于#1477b5ff1，暂不改已审head。拟新增内部ControlledDatabaseCopy：只能接收ControlledVerifier与同一handoff/OwnershipProtocol/显式CohortProbe组成的新副本目标；锁FD、nonce/project、target hash、daemon/claim、完整两DB服务/卷映射及真实network=none/无发布端口必须匹配，目标只能有platform-db/engine-db。重用OwnedMaintenance的真实container_check/ControllerProcessGuard，不以caller布尔或任意CID当归属证明。此组件没有产品restore/CLI调用，签名/有效配置/业务/候选资格继续false。

输入必须先经原verify_backup的HMAC、全inventory、匿名payload格式/归档及版本/schema/floor检查；解密后复核认证control和cipher hash视图不漂移，再一次导出并封印两DB FD。只有该验证器签发的单次typed input能进入新增固定import profile，绑定service/CID、operation和installation；普通lease或caller SHA拒绝。父copy持有source/cipher/输入export及输出workspace，Unknown保留并poison整个copy，不能因with自动退出丢失租约。每个目标预登记四个receipt操作（两项空库预检+PG/Maria import），已存在任何相同receipt拒绝新copy；失败后不能以相同新scope重新执行。

拟固定profiles：PG空表计数/pg_restore单事务且保留source ownership；Maria空表计数/固定secret-file读取到子进程环境（不在argv或日志）+binary-mode client。导入wrapper以管道/coprocess计算输入hash，仅固定SQLtool stdout转有界stderr，等待全部固定子进程结束后发hashack及原nonce-footer；不使用具名明文/临时SQL。增加strict v4 sourceEvidence（受认证control/manifest SHA）及input/terminal审计，GC404未知仍拒绝，hashack/exit0不等于业务提交或事务一致性。

隔离native拟复用真实PG/Maria dump、新scope/卷、58迁移及数据/人员失效验证，真实OpenSSL/memfd/HMAC将snapshot接入新consumer。需区分fixture初始化/查询的旧同步工具与真正受控输入回执，不能泛称整条CLI已covered。known failure用新scope证明PG成功/Maria凭据失败后停止、不重试且source保留；未知Exec/部分输入/日志缺失仅先模型证明阻断与租约保留，真实崩溃或unknown结局不得强制清理或伪造终态。新scope控件handoff仍保留pending，组件的显式清理只有完整资源+cohort已知结束才可做。实际native、调用/目标签名配置、业务资格均待实现和独立复核，不在本段标通过。


### 2026-11-12：认证双库输入实现及待运行隔离证据（接续上一设计）

本分支实现内部 ControlledDatabaseCopy/NewCopyTarget，复用实际 ControlledVerifier 的 HMAC、完整密文 inventory、匿名解密、schema/version/floor 检查；控制文件先通过 no-follow/普通单链接/属主私有权限/大小预检才读取，解密后再认证并在输入使用时复核源视图。真实 OwnershipProtocol/CohortProbe、原 handoff 锁和四个预登记 operation 绑定两 DB 服务/CID/installation；完整资源映射、无网络/发布端口、非特权及有限资源检查拒绝漂移。typed 单次输入方可进入固定 PG/Maria profile，普通 sealed lease 加调用方 SHA 拒绝。v4 source/input/terminal 回执只记录摘要与固定阶段，不记录 SQL、口令或原始 stderr。

PG 使用固定 Unix socket、pg_restore 单事务/exit-on-error 并保留 ownership；Maria 固定 Unix socket、binary-mode、no-defaults、禁 reconnect/local-infile，口令仅由固定容器 secret-file 进入子进程环境。先前 coproc FD 在 tee 子进程中不可见已真实复现，改为动态 process-substitution FD 后同隔离边界的固定 cat 消费器 exit0/hash ack/清理通过；该小实验不是数据库证明。双向输入继续使用 #146 同线程有界传输；PG 成功/Maria 非零阻止重放，unknown 保留输入/输出、pending 和整个 copy context，不能清理。清理夹具先对全部 scope 预检再删除，任一异常停止，不能因目标未知继续删除源。

本地13项新测试及73项相关组合通过，涵盖 HMAC 拒绝零 DB exec、控制文件 symlink 不读取 referent、源/目标漂移、caller 伪输入、v4严格回执、已知部分成功不重放、未知保留租约、清理双scope预检及严格资格。真实数据库隔离 job 已接 production-smoke，仅 contents:read，固定镜像、新临时scope、无宿主挂载或端口；正向与 known-partial 两次依次运行，JSON证据单列源码hash与cleanup。新增job尚未执行，不可写成通过。

边界：source fixture 生成真实 dump 后曾经过宿主内存；源 cohort/初始化/结果查询和授权失效 reset 仍是明确夹具，目标输入本身使用真实 controller/kernel reader。SQL为合成业务行，未证明跨库事务快照、应用登录、真实崩溃/unknown settle。HMAC不等于 source/target 镜像签名，metadata资源隔离不等于有效产品配置审批。目标签名、配置、业务提交、人员失效与候选/恢复资格均保持 false；独立SQL查询/reset证据不自动改变组件报告。新守卫/交接/复制模块尚未加入产品runtime打包，无 restore/CLI 调用，无门禁解除，不改 Claude Release。


#148非作者复现使用时密文漂移：空库预检后原地翻转postgres.dump.enc而control不变，旧实现仍用先前认证sealed明文继续。不是未认证SQL进入，但不满足此前源漂移声明。新增确定性红测后，每次typed input claim在固定control/源release/floor复核之外，对先前HMAC认证的全密文inventory逐项no-follow FD重算SHA，检查读取前后FD/路径身份、长度与受限字节数；不能在plaintext child lease期间重开workspace key，故复用先前完整认证且当前字节未变的control绑定，不重解密或新增crypto child。首次import前漂移拒绝零SQL；PG完成后改变Maria密文则Maria未开始，整copy失败且不重放PG。15项新测试/75项相关组合通过。每次claim重读全inventory，成本受workspace上限约束但无新增硬时限保证；可信独占controller和最终check-to-use非原子边界不变。旧head实际AMD64 artifact13949183939已下载核对ZIP摘要/18源码+58迁移库存，正常/已知部分失败/cleanup通过；不能代替本修订head线上证据。所有gate保持关闭。

### 2026-11-12：受验签目标与隔离数据库绑定（stack #148）

新增内部 RecoveryTargetBinding，以明确选定的同仓候选 assets、版本、commit、manifest SHA 调用既有完整 verify_release.sh；固定 repository/tag/commit、180秒子进程上限、丢弃工具原始输出，不新增凭据或修改 Release。资产普通单链接/no-follow、属主/不可组写、最多64项及8GiB单文件/16GiB总量，前后身份和SHA一致。复用 source_contract 的 retained bundle/generated 文件校验、实际 Compose/local image 投影；staged manifest/commit 必须匹配受验签资产，快照深拷贝在使用时复比。

ControlledDatabaseCopy.execute_bound 只接该类型，源认证前和每次输入 claim 前检查实际 NewCopyTarget 及数据库 image ID 映射。普通 execute 组件路径不获得签名声明；bound 返回 targetReleaseSignaturesVerified，但完整 targetSignatureVerified/targetConfigurationVerified、业务、人员、restore/candidate 资格仍 false。配置快照和数据库镜像匹配不证明目标口令、角色、全部应用配置已安全；数据库一完成后绑定漂移会阻止数据库二，不重放。

缺接口红测后，7项新测试/70项相关组合通过。验签工具/Compose/target 为明确模型 seam，覆盖固定命令、非零/超时、资产/manifest/commit/配置漂移、symlink及逐输入复核；没有新受信目标的真实 bound 正向证据。#148新84bde33d的 artifact13949837988 已核 ZIP f9e527bcbdf802cf3609d0f95a042508bc5eb68388dcef8cd3a64489182a5ebc、18源码+58迁移库存和两份JSON/cleanup，只证明 unbound 双库组件。

新模块尚未加入受校验 runtime 打包/导入清单，无产品 restore/CLI 调用。可信独占 controller、最终检查到使用非原子、重复扫描成本边界不变。Claude/协调器仍需明确包含已审模块的冻结目标、同仓run/commit/manifest/固定digest/两架构bundle及完整验签来源，再完成打包、真实bound导入、受控授权失效、业务/故障/四平台验收。不把旧rc19当作新目标通过，不触发候选/Release，不解除门禁。

#149非作者真实复现验签超时只杀直接bash、后代仍写sentinel。保留原语义抽取后的真实fork红测同样失败，再改Popen独立session/close_fds/三路DEVNULL：180秒验签预算，超时后leader尚未reap时仅一次SIGKILL原进程组，额外最多3秒wait及组缺席观察；失败/观察未知转TargetVerificationUncertain（UncertainExec），异常保留process上下文、不重放。正常leader退出而组仍在时不再向可能复用的PGID发送信号，保守未知，即使exit0也不得继续。真实本机测试证明该固定后代不能延迟写入、继承性外来FD未进child且parent仍持有、stdout/stderr无PIPE；模型覆盖kill拒绝及exit0残留观察未知。10项本包/73相关组合通过，需新head Linux CI。进程组不是cgroup或任意恶意daemon containment，主动setsid逃逸未证明；不可据此声称所有后代安全停止。完整恢复/打包及门禁不变。

#149后续非作者确认SIGINT取消边界：Popen.wait内部可能已reap leader再抛KeyboardInterrupt，不能在BaseException中按旧PGID发送信号。真实SIGINT新红测确认returncode=0时原实现仍调用killpg。修订仅在TimeoutExpired未reap路径进行一次组终止；其他wait/启动/最终观察中断统一TargetVerificationUncertain，保留能取得的process（启动中断可能无handle），不poll后发信号、不自动重试。真实SIGINT新版不再发组信号，未伤其他组；模型覆盖未reap取消及启动/末尾观察中断。13项本包/76相关回归通过。取消时不能承诺后代已停止，未知context/资产必须保留，产品恢复及清理不得因该失败继续；这不是全量后代containment证明，门禁保持。
