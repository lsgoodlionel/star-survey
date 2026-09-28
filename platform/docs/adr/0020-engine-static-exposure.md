# ADR 0020：答卷附件的匿名访问防护放进服务器配置，不再依赖 `.htaccess`

- 状态：已实施；探针脚本在两种 `AllowOverride` 下都验证过
- 日期：2026-09-28
- 关联：P0 发现 11；[ADR 0002](0002-tenancy.md) 已知限制第 3 条
- 证据：`platform/tests/e2e/attachment_guard.py`（`platform/deploy/test/run-attachment-guard.sh`）

## 背景

引擎自带两份 `.htaccess` 保护答卷附件与运行时目录：

- `upload/surveys/.htaccess` 只拦文件名匹配 `^fu_[a-z0-9_]*$` 的文件（已提交的答卷附件）；
- `tmp/runtime/.htaccess` 拦运行时日志与缓存；根 `.htaccess` 另有一批 `RedirectMatch`。

`.htaccess` 是 Apache 特有的，而且**只在 `AllowOverride` 打开时才被读取**。
Debian 的 `apache2.conf` 对 `/var/www/` 的默认值就是 `AllowOverride None`——
本镜像之所以生效，纯粹因为上游 `php:8.3-apache` 的 `conf-enabled/docker-php.conf`
又把它设回了 `All`。换 nginx、或有人按安全基线收紧 `AllowOverride`，
**知道 sid 与存储名即可匿名下载任意一份他人的答卷附件**。

复现（修复前，`GUARD_IMAGE=survey-web platform/deploy/test/run-attachment-guard.sh`）：
同一组探针跑两遍，8 条不符合期望。

| 探针 | 镜像现状（`.htaccess` 生效） | `AllowOverride None` |
|---|---|---|
| 答卷附件 `fu_*` | 403 | **200，正文就是附件内容** |
| 在途上传 `futmp_*`（`tmp/upload/`） | **200** | **200** |
| 在途上传 `futmp_*`（附件目录） | **200** | **200** |
| 引擎运行时日志 / 缓存 | 403 | **200** |

顺带查实的第二件事：`.htaccess` 全程生效时，**在途上传 `futmp_*` 本来就没被任何规则挡住**。
`UploaderController.php:187` 把上传落到 `tempdir/upload/futmp_<随机>_<ext>`，那一层没有 `.htaccess`，
引擎自带的规则也只匹配 `^fu_`。这条是这次顺带发现的、当前部署下**已经成立**的泄露。

## 决定

1. **把判据搬进服务器配置**：`platform/deploy/dev/engine-static-guard.conf`，
   由 `Dockerfile` 装进 `/etc/apache2/conf-enabled/zz-engine-static-guard.conf`。
   放在 `conf-enabled` 里，`AllowOverride` 与 `.htaccess` 都不影响它。
   文件名的 `zz-` 前缀保证它在上游 `docker-php.conf` 之后加载。
2. **判据仍然按文件名**（`fu_` 与 `futmp_`），不按目录。原因见「为什么不移出 web 根」。
   同时对 `upload/` 关掉目录列举——附件名是随机串，能列目录等于把名字送出去。
3. **`tmp/` 整体不对外，只放行 `tmp/assets`**：Yii 把前端 JS/CSS 发布到 `tmp/assets`
   （`LSYii_Application.php:117-127`，`tempurl + '/assets'`），那一份必须公开；
   `tmp/upload/`（在途上传）、`tmp/runtime/`（`application.log`、`plugin.log`、缓存）、
   tcpdf 缓存一律拒绝。
4. **nginx 部署有对应的一份**：`platform/deploy/dev/engine-static-guard.nginx.conf`，
   include 进引擎的 server 块。它不是可选项——nginx 根本不读 `.htaccess`。
   探针脚本的第 3 遍**真的起一个 nginx 容器**把同一组探针跑一遍，
   否则这份片段就只是"看上去对"的配置：nginx 的 location 优先级（`^~` 会短路正则）
   写错了等于没防，而那种错误在纸面上看不出来。
5. **开机自检**：`platform/deploy/private/preflight.sh` 新增一节，往实例里放一个
   `fu_` 金丝雀再匿名 GET 它，取不到才算通过。ADR 0002「把决定 3 写进交付部署脚本并加开机自检」
   这条待办，附件这一项由此落地。
6. **验证方式是"三种服务器形态各跑一遍，三遍都必须拒绝"**（Apache 默认、
   Apache ＋ `AllowOverride None`、nginx）。只在默认配置下通过说明不了任何事——
   那只证明 `.htaccess` 被读到了。

## 为什么不把上传目录移出 web 根

看上去更彻底，但做不到：答卷附件与**问卷资源**落在同一个目录里。
KCFinder 的 `uploadURL` 是 `upload/surveys/<sid>/`（`htmleditor_helper.php:73`），
管理员通过资源面板上传的 PDF、图片会进 `upload/surveys/<sid>/files/` 与 `.../images/`，
并被题干直接以该 URL 引用——它们**必须**公开。两者只靠文件名区分：
答卷附件是引擎生成的 `fu_<随机>`（`em_manager_helper.php:8809`），问卷资源保留原名。

把整个 `upload/surveys` 移出 web 根等于让问卷渲染不出图。把 `uploaddir` 指到别处同理。
所以只能沿用引擎自己的判据（文件名），把它搬到一个不依赖 `AllowOverride` 的位置。

探针里因此有三条**正对照**：问卷图片、与附件同目录的问卷资源、`tmp/assets` 下的前端资源，
它们必须照旧可取。拦过头的"修复"会让问卷渲染不出来，而那种失败在单元测试里看不见。

## 代价与残留

- **判据是文件名，不是权限**。引擎将来若改了存储名前缀，这份守卫会**静默失配**。
  探针脚本把 `fu_` / `futmp_` 两个前缀钉死，改了就红。
- **按名匹配刻意做成大小写不敏感**（Apache `(?i)`、nginx `~*`），比引擎自带的
  `^fu_[a-z0-9_]*$` 更宽：大小写不敏感的文件系统（开发机的 macOS 卷）上
  `GET .../FU_abc` 照样能取到 `fu_abc`，大小写敏感的规则在那里等于没写。
  代价是一份**恰好叫 `Fu_...` 的问卷资源**会被一起拦掉——概率极低，
  而且它在引擎自带规则下本来也处在同一片灰色地带。同时关掉 `MultiViews`：
  内容协商会让 `fu_abc` 这个请求命中 `fu_abc.pdf`，把按名拒绝绕过去。
- **平台侧的取件不受影响**：平台走插件通道取附件（[ADR 0018](0018-gateway-plugin-channel.md)、
  `response-read-v1` 附件取件），管理端走 `responses/downloadfile` 路由（带权限检查）。
  两条路都不经过静态文件，因此这次收紧不影响任何既有功能。
- **共享镜像要重建才生效**：守卫是镜像的一部分。已经在跑的栈用的是旧 `survey-web`，
  需要重新 `docker compose -f docker-compose.dev.yml build web`。
  探针脚本自己构建一个车道专用标签（`survey-web-guard:test`），不去覆盖别的栈在用的镜像。
- **同一类问题还有没修的**：根 `.htaccess` 还挡着 `vendor/`、`docs/`、隐藏文件与
  `setdebug.php`，它们同样只在 `AllowOverride` 打开时有效。本车道只收口答卷附件与运行时目录
  （这是记录在案的那条缺口），`vendor/` 与 `docs/` 的同类暴露**本次未修**，留给后续车道。
- **未覆盖**：CDN / 反向代理在引擎前面缓存静态文件的形态；`upload/labels/`（标签集资源）。

## 验证

```
platform/deploy/test/run-attachment-guard.sh          # 绿：Apache 两种形态 ＋ nginx 都拒绝
GUARD_IMAGE=survey-web platform/deploy/test/run-attachment-guard.sh   # 红：修复前的镜像
```

探针容器不挂载仓库、不需要数据库：文件全部写进容器自己的可写层，
第 1 遍另把仓库里那几份 `.htaccess` 原样拷进去，好让"镜像现状"这一遍如实反映真实部署。
第 3 遍用 `nginx:alpine`，只挂载交付物里的那份片段与一个最小 server 块。
