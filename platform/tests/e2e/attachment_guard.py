#!/usr/bin/env python3

"""答卷附件匿名下载防护的端到端验证（在宿主机运行，见 run-attachment-guard.sh）。

复现的缺口（P0 发现 11 / ADR 0002 已知限制、ADR 0020）：
``upload/surveys/<sid>/files/fu_*`` 的匿名访问保护**只来自引擎自带的 `.htaccess`**。
`.htaccess` 是 Apache 特有的，而且只在 ``AllowOverride`` 打开时才被读取——
换 nginx、或按 Debian 的 ``apache2.conf`` 默认值（``AllowOverride None``）部署，
知道 sid 与存储名即可匿名下载任意一份他人答卷附件。

本脚本因此**跑三遍同一组探针**：

1. 「按镜像现状」：上游 ``php:8.3-apache`` 的 ``docker-php.conf`` 把 ``AllowOverride`` 打开了，
   引擎的 `.htaccess` 生效；
2. 「AllowOverride None」：注入一段服务器配置把 ``.htaccess`` 关掉，等价于收紧后的 Apache；
3. 「nginx」：换一个 nginx 容器，只 include 交付物里的 ``engine-static-guard.nginx.conf``。
   nginx 根本不读 `.htaccess`，所以这一遍验的就是发现 11 原话里的"换 nginx"那个场景。

**三遍都必须拒绝**。这正是修复前后的分水岭：修复前第 2 遍返回 200＋附件正文（红），
修复后三遍都是 403（绿）。只在第 1 遍里通过说明不了任何事——那只证明 `.htaccess` 被读到了。

同时验证**没有过度拦截**：问卷图片、问卷资源（非 ``fu_`` 命名的同目录文件）、
Yii 发布到 ``tmp/assets`` 的 JS/CSS 必须照旧可取。把它们一起拦掉的"修复"会让问卷渲染不出来。

不挂载仓库：探针文件全部由 ``docker exec`` 写进容器自己的可写层，
所以既不污染工作树，也不依赖数据库或已安装的引擎。为了让第 1 遍如实反映"引擎自带的
`.htaccess` 到底挡住了什么"，脚本会把仓库里那几份 `.htaccess` 原样拷进容器。

怎么亲眼看它红：把 ``--image`` 指向**不带守卫的**引擎镜像即可，例如

    python3 platform/tests/e2e/attachment_guard.py --image survey-web \\
        --htaccess-root .

修复前（镜像里没有 conf-enabled/zz-engine-static-guard.conf）第 2 遍会给出
``答卷附件 fu_*：期望被拒，实得 HTTP 200，且正文里有探针内容``。
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

#: 容器里的站点根，与 survey-web 镜像的 DocumentRoot 一致。
DOCROOT = "/var/www/html"
#: 探针用的引擎问卷号；随便一个不会与真实问卷冲突的值即可（本脚本不碰数据库）。
PROBE_SID = 999888
#: 关掉 .htaccess 的服务器配置（等价于 nginx / AllowOverride None 的部署）。
NO_OVERRIDE_CONF = "<Directory {}/>\n\tAllowOverride None\n</Directory>\n".format("/var/www")
NO_OVERRIDE_PATH = "/etc/apache2/conf-enabled/zz-e2e-no-override.conf"
#: 引擎自带的 .htaccess，第 1 遍要如实带上——否则"镜像现状"这一遍会把
#: 本来由 .htaccess 挡住的东西也报成漏洞。相对仓库根。
ENGINE_HTACCESS = (".htaccess", "upload/surveys/.htaccess", "tmp/runtime/.htaccess")


class ProbeFailure(AssertionError):
    """有探针没有得到期望结果。"""


def info(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class Probe:
    """一条探针：往容器里放一个文件，再从容器内匿名 GET 它。"""

    label: str
    #: 相对站点根的路径；以 ``/`` 结尾表示只探目录（不放文件）。
    path: str
    #: 期望可取（正对照）还是必须被拒（负对照）。
    expect_public: bool


#: 必须拦住的：答卷附件、在途上传、运行时日志、附件目录列举。
DENIED_PROBES: List[Probe] = [
    Probe("答卷附件 fu_*", "upload/surveys/{}/files/fu_e2eguard".format(PROBE_SID), False),
    Probe("答卷附件 fu_* 带扩展名", "upload/surveys/{}/files/fu_e2eguard_pdf".format(PROBE_SID), False),
    Probe("在途上传 futmp_*（同目录）",
          "upload/surveys/{}/files/futmp_e2eguard_pdf".format(PROBE_SID), False),
    Probe("在途上传 futmp_*（tmp/upload）", "tmp/upload/futmp_e2eguard_pdf", False),
    Probe("引擎运行时日志", "tmp/runtime/application.log", False),
    Probe("引擎运行时缓存", "tmp/runtime/cache/e2eguard.bin", False),
    Probe("附件目录列举", "upload/surveys/{}/files/".format(PROBE_SID), False),
    # 大小写不敏感的文件系统上，FU_ 能取到 fu_：规则必须一起拦住。
    Probe("答卷附件 FU_*（大小写变体）", "upload/surveys/{}/files/FU_e2eguard".format(PROBE_SID), False),
]

#: 必须照旧可取的：拦过头就会让问卷渲染不出来。
PUBLIC_PROBES: List[Probe] = [
    Probe("问卷图片（题干里引用）", "upload/surveys/{}/images/e2eguard.png".format(PROBE_SID), True),
    Probe("问卷资源（非 fu_ 命名，与附件同目录）",
          "upload/surveys/{}/files/e2eguard-resource.pdf".format(PROBE_SID), True),
    Probe("Yii 发布的前端资源", "tmp/assets/e2eguard/probe.js", True),
]


def docker(args: List[str], check: bool = True) -> str:
    result = subprocess.run(["docker"] + args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError("docker {} failed: {}".format(" ".join(args), result.stderr.strip()))
    return result.stdout


class ProbeContainer:
    """一个只用来做静态文件探针的一次性容器（Apache / 引擎镜像）。"""

    def __init__(self, name: str, image: str, mounts: Optional[List[str]] = None):
        self._name = name
        self._image = image
        self._mounts = mounts or []

    def __enter__(self) -> "ProbeContainer":
        docker(["rm", "-f", self._name], check=False)
        run = ["run", "-d", "--name", self._name]
        for mount in self._mounts:
            run += ["-v", mount]
        docker(run + [self._image])
        self._await_server()
        return self

    def __exit__(self, *_exc) -> None:
        docker(["rm", "-f", self._name], check=False)

    def _await_server(self) -> None:
        for _ in range(60):
            # 空站点根下 / 是 403/404，同样说明服务器已经在听了。
            if self.status("") in ("200", "403", "404"):
                return
            time.sleep(0.5)
        raise RuntimeError("the web server in {} never started answering".format(self._name))

    def exec_ok(self, argv: List[str]) -> bool:
        result = subprocess.run(["docker", "exec", self._name] + argv, capture_output=True, text=True)
        return result.returncode == 0

    def exec_out(self, argv: List[str]) -> str:
        return docker(["exec", self._name] + argv)

    # ------------------------------------------------------------ 探针布置

    def place(self, path: str, marker: str) -> None:
        """在站点根下放一个内容为 marker 的文件（目录探针只建目录）。"""
        full = "{}/{}".format(DOCROOT, path.rstrip("/"))
        if path.endswith("/"):
            self.exec_out(["mkdir", "-p", full])
            return
        parent = full.rsplit("/", 1)[0]
        self.exec_out(["mkdir", "-p", parent])
        # 用 tee 写文件，避免把内容塞进 shell 命令行。
        subprocess.run(["docker", "exec", "-i", self._name, "tee", full],
                       input=marker, capture_output=True, text=True, check=True)

    def status(self, path: str) -> str:
        out = self.exec_out(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                             "http://127.0.0.1/{}".format(path)])
        return out.strip()

    def body(self, path: str) -> str:
        return self.exec_out(["curl", "-s", "http://127.0.0.1/{}".format(path)])

    # ------------------------------------------------------------ 配置切换

    def copy_in(self, host_path: str, container_path: str) -> None:
        docker(["cp", host_path, "{}:{}".format(self._name, container_path)])

    def write_conf(self, container_path: str, content: str) -> None:
        subprocess.run(["docker", "exec", "-i", self._name, "tee", container_path],
                       input=content, capture_output=True, text=True, check=True)

    def reload(self) -> None:
        self.exec_out(["apachectl", "-k", "graceful"])
        # graceful 是异步的；等到新配置真的在服务为止。
        for _ in range(60):
            if self.status("") in ("200", "403", "404"):
                return
        raise RuntimeError("apache did not come back after a graceful reload")


#: nginx 探针用的 server 块。DOCROOT 与 Apache 那边一致，探针集合因此可以整套复用。
NGINX_SERVER = """server {{
    listen 80;
    server_name _;
    root {docroot};
    include /etc/nginx/snippets/engine-static-guard.conf;
    location / {{
    }}
}}
"""


class NginxProbeContainer(ProbeContainer):
    """同一组探针，换到 nginx 上再跑一遍（P0 发现 11 说的正是"换 nginx"这个场景）。

    nginx:alpine 里没有 curl，用 busybox 的 wget：``-S`` 把应答头打到 stderr，
    第一行 ``HTTP/1.1 <码> <原因>`` 就是状态码。
    """

    def __init__(self, name: str, image: str, guard_conf: str, tmp_dir: str):
        server_conf = os.path.join(tmp_dir, "engine-static-guard-server.conf")
        with open(server_conf, "w", encoding="utf-8") as handle:
            handle.write(NGINX_SERVER.format(docroot=DOCROOT))
        super().__init__(name, image, mounts=[
            "{}:/etc/nginx/snippets/engine-static-guard.conf:ro".format(os.path.abspath(guard_conf)),
            "{}:/etc/nginx/conf.d/default.conf:ro".format(os.path.abspath(server_conf)),
        ])

    def _fetch(self, path: str) -> str:
        result = subprocess.run(
            ["docker", "exec", self._name, "wget", "-S", "-O", "-", "http://127.0.0.1/{}".format(path)],
            capture_output=True, text=True)
        return result.stdout + "\n" + result.stderr

    def status(self, path: str) -> str:
        for line in self._fetch(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("HTTP/"):
                parts = stripped.split()
                return parts[1] if len(parts) > 1 else "0"
        return "0"

    def body(self, path: str) -> str:
        result = subprocess.run(
            ["docker", "exec", self._name, "wget", "-q", "-O", "-", "http://127.0.0.1/{}".format(path)],
            capture_output=True, text=True)
        return result.stdout


def place_engine_htaccess(container: ProbeContainer, repo_root: Path) -> None:
    """把引擎自带的 .htaccess 原样拷进容器，让第 1 遍反映真实的"镜像现状"。"""
    for relative in ENGINE_HTACCESS:
        source = repo_root / relative
        if not source.is_file():
            raise RuntimeError("engine .htaccess missing: {}".format(source))
        target = "{}/{}".format(DOCROOT, relative)
        container.exec_out(["mkdir", "-p", target.rsplit("/", 1)[0]])
        container.copy_in(str(source), target)


def run_probes(container: ProbeContainer, phase: str, marker: str) -> List[str]:
    """跑完一轮探针，返回不符合期望的说明（空表＝这一轮通过）。"""
    failures: List[str] = []
    for probe in DENIED_PROBES + PUBLIC_PROBES:
        container.place(probe.path, marker)
    for probe in DENIED_PROBES:
        status = container.status(probe.path)
        leaked = marker in container.body(probe.path)
        if status == "200" or leaked:
            failures.append("[{}] {}：期望被拒，实得 HTTP {}{}".format(
                phase, probe.label, status, "，且正文里有探针内容" if leaked else ""))
        else:
            info("  拒绝  [{}] {} -> HTTP {}".format(phase, probe.label, status))
    for probe in PUBLIC_PROBES:
        status = container.status(probe.path)
        if status != "200":
            failures.append("[{}] {}：期望可取，实得 HTTP {}（拦过头了）".format(
                phase, probe.label, status))
        else:
            info("  可取  [{}] {} -> HTTP 200".format(phase, probe.label))
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="survey-web", help="引擎镜像（默认 survey-web）")
    parser.add_argument("--name", default="survey-attachment-guard-probe", help="探针容器名")
    parser.add_argument("--htaccess-root", default=None,
                        help="仓库根；给了就把引擎自带的几份 .htaccess 原样拷进容器")
    parser.add_argument("--nginx-guard", default=None,
                        help="nginx 版守卫的路径；给了就在 nginx 上把同一组探针再跑一遍")
    parser.add_argument("--nginx-image", default="nginx:alpine", help="nginx 镜像")
    args = parser.parse_args(argv)

    marker = "attachment-guard-canary-{}".format(uuid.uuid4().hex)
    failures: List[str] = []
    with ProbeContainer(args.name, args.image) as container:
        if args.htaccess_root:
            place_engine_htaccess(container, Path(args.htaccess_root))
        info("第 1 遍：按镜像现状（引擎 .htaccess 生效）")
        failures += run_probes(container, "镜像现状", marker)

        info("第 2 遍：AllowOverride None（收紧后的 Apache，.htaccess 被忽略）")
        container.write_conf(NO_OVERRIDE_PATH, NO_OVERRIDE_CONF)
        container.reload()
        failures += run_probes(container, "AllowOverride None", marker)

    if args.nginx_guard:
        info("第 3 遍：nginx（它根本不读 .htaccess，守卫必须由 include 的片段提供）")
        with tempfile.TemporaryDirectory() as tmp_dir:
            with NginxProbeContainer(args.name + "-nginx", args.nginx_image,
                                     args.nginx_guard, tmp_dir) as nginx:
                failures += run_probes(nginx, "nginx", marker)

    if failures:
        for line in failures:
            print("  失败  {}".format(line), file=sys.stderr)
        raise ProbeFailure("{} 条探针不符合期望；答卷附件的防护不能只靠 .htaccess".format(len(failures)))
    info("attachment guard e2e passed（Apache 两种 AllowOverride 与 nginx 下附件都取不到，且正对照未被拦）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
