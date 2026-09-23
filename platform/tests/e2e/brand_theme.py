#!/usr/bin/env python3

"""WP-19 切片 19.3 品牌主题与多语言端到端：定义带 branding / translations，经网关发布到真引擎。

在宿主机运行，见 platform/deploy/test/run-brand-theme.sh。场景（契约 survey-branding-v1）：

T  主题安装：zh-business 进 lime_templates ＋ 全局 lime_template_configuration，
   引擎的 getTemplateList() 认得它（认不得的话 LSS 的 <themes> 小节会被整段跳过）。
B1 发布一份带品牌的双语问卷：按问卷的主题选项落到 lime_template_configuration（sid=本问卷）。
B2 作答页带上品牌：页脚文本、logo、主色变量、浏览器标题。
A  去广告：整页 HTML 里没有任何**外部来源**的绝对地址；没有 generator 元标签、
   没有 limesurvey.org、没有 Google Analytics 代码段。
L1 语言切换：作答页列出两种语言，?lang=zh-Hans 显示译文。
L2 回退：没有译文的题目显示基础语言原文，不是空白。
X  转义：品牌文本里的 <script> 在页面上是字面量，不是可执行标签。
U  没有 branding 的问卷不受影响：不产生按问卷的主题配置行。
"""

import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.branding.schema import BRAND_THEME  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

INSTALL_THEME = "platform/tools/engine-theme/install-survey-theme.php"
BASE_LANGUAGE = "en"
OTHER_LANGUAGE = "zh-Hans"
BRAND_COLOR = "#1f6feb"
BRAND_FOOTER_EN = "(c) 2026 Acme Ltd."
BRAND_FOOTER_ZH = "（c）2026 艾克姆有限公司"
BRAND_TITLE_EN = "Acme feedback"
BRAND_TITLE_ZH = "Acme 意见征集"
INJECTED = '<script>window.__mjy_brand_xss=1</script>'
TRANSLATED_QUESTION = "你最近怎么样？"
BASE_QUESTION = "How have you been?"
UNTRANSLATED_QUESTION = "Anything to add?"

#: 作答页允许出现的来源：只有它自己。任何别的主机都是"第三方"。
OWN_HOSTS = frozenset({"localhost", "127.0.0.1"})

#: 页面里所有可能带地址的位置。样式里的 url() 单独扫，它是最容易被忽略的外连通道。
_URL_ATTRIBUTE = re.compile(r"""(?:href|src|srcset|action|poster|data-src)\s*=\s*["']([^"']+)["']""", re.I)
_CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)""", re.I)
_ABSOLUTE = re.compile(r"\A(?:(?P<scheme>https?):)?//(?P<host>[^/:?#]+)", re.I)


class Context:
    def __init__(self, container: str, db: Database, driver: str):
        self.container = container
        self.db = db
        self.driver = driver
        self.failures: List[str] = []

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label, "" if condition else ": {}".format(detail)),
              file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def fetch(self, path: str) -> str:
        completed = subprocess.run(
            ["docker", "exec", self.container, "curl", "-s", "--max-time", "30", "http://localhost" + path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise RuntimeError("fetch {} failed: {}".format(path, completed.stderr.decode("utf-8", "replace")))
        return completed.stdout.decode("utf-8", "replace")

    def page(self, survey_id: int, language: str) -> str:
        return self.fetch("/index.php/{}?newtest=Y&lang={}".format(survey_id, language))

    def theme_options(self, survey_id: int) -> Dict[str, str]:
        """按问卷的主题选项。没有行、或引擎写的是 'inherit' / 空，都当作"没有选项"。"""
        rows = self.db.rows(
            "SELECT options FROM lime_template_configuration WHERE sid = {}".format(survey_id)
        )
        if not rows or not rows[0]:
            return {}
        raw = rows[0][0].strip()
        if raw in ("", "NULL", "inherit", '""'):
            return {}
        if self.driver == "mysql":
            # mariadb 的批处理输出把反斜杠成对输出（`\/` → `\\/`），先还原再解析 JSON。
            raw = raw.replace("\\\\", "\\")
        return json.loads(raw)


# ------------------------------------------------------------------ 定义


def definition(branded: bool) -> Dict[str, Any]:
    """一道翻译过的题 ＋ 一道没翻译的题，后者用来证明回退。"""
    question_a = str(uuid.uuid4())
    question_b = str(uuid.uuid4())
    payload = {
        "definitionVersion": 1,
        "uuid": str(uuid.uuid4()),
        "title": "Brand e2e",
        "language": BASE_LANGUAGE,
        "theme": BRAND_THEME if branded else "fruity_twentythree",
        "additionalLanguages": [OTHER_LANGUAGE],
        "settings": {
            "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N", "refurl": "N",
            "allowsave": "N", "allowprev": "Y", "alloweditaftercompletion": "N", "format": "G",
            "questionindex": "0",
        },
        "groups": [{
            "uuid": str(uuid.uuid4()),
            "title": "Only group",
            "questions": [
                {"uuid": question_a, "code": "QMOOD", "type": "L", "text": BASE_QUESTION,
                 "answers": [{"code": "A1", "text": "Fine"}, {"code": "A2", "text": "Not great"}]},
                {"uuid": question_b, "code": "QNOTE", "type": "S", "text": UNTRANSLATED_QUESTION},
            ],
        }],
        "translations": {
            OTHER_LANGUAGE: {
                "title": "品牌端到端",
                "questions": {question_a: {"text": TRANSLATED_QUESTION}},
                "answers": [
                    {"question": question_a, "code": "A1", "text": "挺好"},
                    {"question": question_a, "code": "A2", "text": "不太好"},
                ],
            }
        },
    }
    if branded:
        payload["branding"] = {
            "brandingVersion": 1,
            "primaryColor": BRAND_COLOR,
            "pageTitle": {BASE_LANGUAGE: BRAND_TITLE_EN, OTHER_LANGUAGE: BRAND_TITLE_ZH},
            # 故意把脚本塞进品牌文本：页面上必须是字面量。
            "footerText": {BASE_LANGUAGE: BRAND_FOOTER_EN + INJECTED, OTHER_LANGUAGE: BRAND_FOOTER_ZH},
            "logoFile": "logo-demo.png",
            "logoAlt": "Acme",
            "faviconFile": "favicon.ico",
        }
    return payload


def publish(container: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = RemoteControlClient(DockerCurlTransport(container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        return Publisher(client, engine_instance=container).publish(
            SurveyDefinition.from_dict(payload)).to_dict()
    finally:
        client.logout()


def publish_ok(context: Context, payload: Dict[str, Any]) -> int:
    result = publish(context.container, payload)
    if not result["ok"]:
        raise RuntimeError("publish failed: {}".format(json.dumps(result, ensure_ascii=False)[:2000]))
    return int(result["surveyId"])


# ------------------------------------------------------------------ 外部来源


def external_hosts(html: str) -> Set[str]:
    """页面里出现的、不属于作答页自己的主机名。协议相对地址（//host/…）也算。"""
    hosts = set()
    for pattern in (_URL_ATTRIBUTE, _CSS_URL):
        for value in pattern.findall(html):
            match = _ABSOLUTE.match(value.strip())
            if match is None:
                continue  # 相对地址，天然同源
            host = match.group("host").lower()
            if host not in OWN_HOSTS:
                hosts.add(host)
    return hosts


# ------------------------------------------------------------------ 场景


def scenario_install_theme(context: Context) -> None:
    completed = subprocess.run(
        ["docker", "exec", context.container, "php", INSTALL_THEME, BRAND_THEME],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    context.check(
        "T 主题安装脚本成功",
        completed.returncode == 0,
        completed.stderr.decode("utf-8", "replace") + completed.stdout.decode("utf-8", "replace"),
    )
    installed = context.db.value(
        "SELECT COUNT(*) FROM lime_templates WHERE name = '{}'".format(BRAND_THEME))
    context.check("T lime_templates 有这条主题", installed == "1", installed)
    configured = context.db.value(
        "SELECT COUNT(*) FROM lime_template_configuration "
        "WHERE template_name = '{}' AND sid IS NULL AND gsid IS NULL".format(BRAND_THEME))
    context.check("T 有全局主题配置行（getTemplateList 的条件）", configured == "1", configured)
    extends = context.db.value(
        "SELECT extends FROM lime_templates WHERE name = '{}'".format(BRAND_THEME))
    context.check("T 继承关系写对了", extends.strip() == "vanilla", extends)


def scenario_branded_publish(context: Context) -> int:
    survey_id = publish_ok(context, definition(branded=True))
    template = context.db.value("SELECT template FROM lime_surveys WHERE sid = {}".format(survey_id))
    context.check("B1 问卷用的是品牌主题", template.strip() == BRAND_THEME, template)

    options = context.theme_options(survey_id)
    context.check("B1 按问卷的主题配置行已生成", bool(options))
    if not options:
        return survey_id
    context.check("B1 主色下发到主题选项", options.get("mjybrandprimary") == BRAND_COLOR, options.get("mjybrandprimary"))
    # 引擎保存时把主题内的相对路径归一成它自己的"虚拟路径"（image::theme::…，
    # SurveyThemeHelper），渲染时再解回来。关键是它仍然锁在主题目录里。
    logo = options.get("mjybrandlogofile", "")
    context.check("B1 logo 仍然锁在主题目录里",
                  logo.endswith("files/logo-demo.png") and "://" not in logo, logo)
    context.check("B1 页脚按语言各一条键",
                  options.get("mjybrandfooter__" + OTHER_LANGUAGE) == BRAND_FOOTER_ZH,
                  options.get("mjybrandfooter__" + OTHER_LANGUAGE))
    return survey_id


def scenario_brand_on_page(context: Context, survey_id: int) -> None:
    html = context.page(survey_id, BASE_LANGUAGE)
    context.check("B2 页脚文本出现在作答页", BRAND_FOOTER_EN in html)
    context.check("B2 页脚用的是品牌容器", 'id="mjy-brand-footer"' in html)
    context.check("B2 logo 出现在作答页", "logo-demo" in html and 'class="logo img-fluid"' in html)
    context.check("B2 主色注入成 CSS 变量", "--mjy-brand-primary: " + BRAND_COLOR in html)
    context.check("B2 浏览器标题用品牌标题", BRAND_TITLE_EN in html)
    context.check("B2 品牌样式表加载了", "mjy-brand.css" in html)


def scenario_no_third_party(context: Context, survey_id: int) -> None:
    for language in (BASE_LANGUAGE, OTHER_LANGUAGE):
        html = context.page(survey_id, language)
        hosts = external_hosts(html)
        context.check("A 作答页不连外部来源（{}）".format(language), not hosts, sorted(hosts))
        context.check("A 没有 limesurvey.org（{}）".format(language), "limesurvey.org" not in html.lower())
        context.check("A 没有 generator 元标签（{}）".format(language),
                      'name="generator"' not in html.lower())
        lowered = html.lower()
        context.check("A 没有 Google Analytics 代码段（{}）".format(language),
                      "googletagmanager" not in lowered and "google-analytics" not in lowered)


def scenario_languages(context: Context, survey_id: int) -> None:
    english = context.page(survey_id, BASE_LANGUAGE)
    chinese = context.page(survey_id, OTHER_LANGUAGE)

    context.check("L1 语言切换控件出现", "ls-language-link" in english or 'name="lang"' in english)
    context.check("L1 切换列表含另一种语言",
                  OTHER_LANGUAGE in english, "language changer misses " + OTHER_LANGUAGE)
    context.check("L1 html lang 跟着切", 'lang="{}"'.format(OTHER_LANGUAGE) in chinese)
    context.check("L1 译文题干出现", TRANSLATED_QUESTION in chinese)
    context.check("L1 译文选项出现", "挺好" in chinese)
    context.check("L1 基础语言仍是原文", BASE_QUESTION in english and TRANSLATED_QUESTION not in english)
    context.check("L2 未翻译的题回退到基础语言", UNTRANSLATED_QUESTION in chinese)
    context.check("L1 页脚跟着语言走", BRAND_FOOTER_ZH in chinese)
    context.check("L1 浏览器标题跟着语言走", BRAND_TITLE_ZH in chinese)


def scenario_escaping(context: Context, survey_id: int) -> None:
    html = context.page(survey_id, BASE_LANGUAGE)
    context.check("X 注入的脚本没有变成标签", INJECTED not in html)
    context.check("X 注入的脚本是字面量文本", "&lt;script&gt;window.__mjy_brand_xss=1&lt;/script&gt;" in html)


def scenario_unbranded_is_untouched(context: Context) -> None:
    survey_id = publish_ok(context, definition(branded=False))
    # 引擎渲染时会自己给问卷建一行 options='inherit' 的主题配置；关键是里面
    # 没有任何品牌键——没有 branding 的定义不产生任何主题选项。
    options = context.theme_options(survey_id)
    context.check("U 没有 branding 就没有任何品牌选项",
                  not [name for name in options if name.startswith("mjybrand")], sorted(options))
    chinese = context.page(survey_id, OTHER_LANGUAGE)
    context.check("U 多语言在别的主题上一样生效", TRANSLATED_QUESTION in chinese)


# ------------------------------------------------------------------ 入口


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    args = parser.parse_args()

    context = Context(args.container, Database(args.db, args.db_container), args.db)
    scenario_install_theme(context)
    survey_id = scenario_branded_publish(context)
    scenario_brand_on_page(context, survey_id)
    scenario_no_third_party(context, survey_id)
    scenario_languages(context, survey_id)
    scenario_escaping(context, survey_id)
    scenario_unbranded_is_untouched(context)

    print(json.dumps({"surveyId": survey_id, "failures": context.failures}, ensure_ascii=False))
    return 1 if context.failures else 0


raise SystemExit(main())
