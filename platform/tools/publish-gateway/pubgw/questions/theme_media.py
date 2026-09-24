"""媒体类题型主题（切片 02.6）：图片、音频、视频由**平台资产服务**提供。

和之前几类最大的不同在图从哪来。热力图底图、货架图、PK 参赛图至今还是作者在
``themeOptions`` 里填的一串 URL——没有版本、没有归属、协议都不校验。轮播图不走那条路：

* 作者在草稿里只写 ``assetId``；
* 平台发布时解析成 ``url``（签名取件地址）＋ ``assetVersion``（当时的版本号），
  并把引用登记下来（ADR 0019 决定 5）；
* **网关只看见 url**，看见 ``assetId`` 就说明平台没有物化，直接 422。

因此这里的校验只做两件事：地址是不是平台签发的那种形状，以及幻灯片和选项对不对得上。
"""

from typing import Any, Dict, List, Tuple

from ..model import Question
from .theme_kit import (
    CAROUSEL_AUTOPLAY_ATTRIBUTE, CAROUSEL_SLIDES_ATTRIBUTE, Issue, Lowering, OPTION_VALUE,
    canonical_json,
)

#: 一道轮播图最多几张。上限跟着选项走，这里只挡住离谱的值。
MAX_SLIDES = 50
#: 取件地址的长度上限：带签名与过期时刻，比普通链接长一截。
MAX_ASSET_URL = 512
#: 替代文本的长度上限。空的不行——无障碍是 R02-20 的验收项。
MAX_ALT_TEXT = 200
#: 只接受这两种协议。``javascript:``／``data:``／``//`` 开头的一律不是平台签发的地址。
_ALLOWED_SCHEMES = ("https://", "http://")


def _option_codes(question: Question) -> List[str]:
    """本题的选项代码。轮播图只用在单选（``L``）上，选项即答案选项。"""
    return [answer.code for answer in question.answers_on_scale(0)]


def _slide(raw: Any, path: str) -> Tuple[Dict[str, Any], List[Issue]]:
    """一张幻灯片：代码 ＋ 平台签发的地址 ＋ 替代文本 ＋ 被钉死的资产版本号。"""
    if not isinstance(raw, dict):
        return {}, [(OPTION_VALUE, path, "每张幻灯片必须是对象")]
    issues: List[Issue] = []
    if "assetId" in raw:
        # 还留着 assetId 说明平台没有把它解析成取件地址：发出去作答页上的图会全裂。
        issues.append((OPTION_VALUE, path + ".assetId",
                       "资产引用应由平台在发布时解析成 url，定义里不该还留着 assetId"))
    code = raw.get("code")
    if not isinstance(code, str) or not code:
        issues.append((OPTION_VALUE, path + ".code", "幻灯片必须写明它对应哪个选项代码"))
    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith(_ALLOWED_SCHEMES) or len(url) > MAX_ASSET_URL:
        issues.append((OPTION_VALUE, path + ".url",
                       "地址必须是平台签发的 http(s) 取件地址，最长 {} 个字符".format(MAX_ASSET_URL)))
    alt = raw.get("alt")
    if not isinstance(alt, str) or not alt.strip() or len(alt) > MAX_ALT_TEXT:
        issues.append((OPTION_VALUE, path + ".alt",
                       "每张图都要有替代文本（无障碍），最长 {} 个字符".format(MAX_ALT_TEXT)))
    version = raw.get("assetVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        issues.append((OPTION_VALUE, path + ".assetVersion",
                       "必须带平台钉死的资产版本号，日后才说得清当时给作答者看的是哪一版"))
    if issues:
        return {}, issues
    return {"code": code, "url": url, "alt": alt, "assetVersion": version}, []


def _slides(question: Question, values: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Issue]]:
    raw_slides = values.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        return [], [(OPTION_VALUE, "themeOptions.slides", "必须是非空数组")]
    if len(raw_slides) > MAX_SLIDES:
        return [], [(OPTION_VALUE, "themeOptions.slides", "最多 {} 张".format(MAX_SLIDES))]
    slides: List[Dict[str, Any]] = []
    issues: List[Issue] = []
    for index, raw in enumerate(raw_slides):
        parsed, found = _slide(raw, "themeOptions.slides[{}]".format(index))
        issues.extend(found)
        if parsed:
            slides.append(parsed)
    if issues:
        return [], issues
    issues.extend(_coverage_issues(question, slides))
    return ([], issues) if issues else (slides, [])


def _coverage_issues(question: Question, slides: List[Dict[str, Any]]) -> List[Issue]:
    """幻灯片必须**恰好覆盖**本题的全部选项：少一张那个选项就是张空白图，
    多一张则是个点不到的选项，两种都是拼错了，别让它在页面上悄悄消失。"""
    codes = _option_codes(question)
    listed = [item["code"] for item in slides]
    issues: List[Issue] = []
    seen = set()
    for index, code in enumerate(listed):
        path = "themeOptions.slides[{}].code".format(index)
        if code in seen:
            issues.append((OPTION_VALUE, path, "选项代码重复：{}".format(code)))
        seen.add(code)
        if code not in codes:
            issues.append((OPTION_VALUE, path, "本题没有这个选项：{}".format(code)))
    missing = [code for code in codes if code not in seen]
    if missing:
        issues.append((OPTION_VALUE, "themeOptions.slides",
                       "这些选项还没有配图：{}".format("、".join(missing))))
    return issues


def check_carousel(question: Question, values: Dict[str, Any]) -> List[Issue]:
    _slides_value, issues = _slides(question, values)
    return issues


def lower_carousel(question: Question, values: Dict[str, Any]) -> Lowering:
    """幻灯片整体降成一个题目属性；主题在作答页上按它渲染。

    属性里留着 ``assetVersion``：日后回答「当时给作答者看的是哪一版图」靠的就是它，
    而不是去猜资产库现在是第几版。
    """
    slides, _issues = _slides(question, values)
    return Lowering(attributes={
        CAROUSEL_SLIDES_ATTRIBUTE: canonical_json(slides),
        CAROUSEL_AUTOPLAY_ATTRIBUTE: "1" if values.get("autoplay") else "0",
    })
