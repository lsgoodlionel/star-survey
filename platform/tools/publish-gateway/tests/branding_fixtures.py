"""品牌与多语言测试的共用零件（WP-19，契约 survey-branding-v1）。"""

import copy

from pubgw.model import SurveyDefinition

from .fixtures import sample_payload

BRAND_THEME = "zh-business"


def full_branding():
    """每一类品牌取值都用上的块。"""
    return {
        "brandingVersion": 1,
        "primaryColor": "#1F6FEB",
        "pageTitle": {"en": "Acme feedback", "zh-Hans": "Acme 意见征集"},
        "footerText": {"en": "(c) Acme Ltd.", "zh-Hans": "（c）艾克姆有限公司"},
        "logoFile": "logo.png",
        "logoAlt": "Acme",
        "faviconFile": "favicon.ico",
    }


def sample_translations():
    """把样例定义的第一题、它的两个选项与第一个题组译成 zh-Hans。"""
    return {
        "zh-Hans": {
            "title": "网关样例",
            "description": "大约两分钟",
            "groups": {"grp-1": {"title": "基本情况", "description": "简单几题"}},
            "questions": {"q-single": {"text": "选一个（中文）", "help": "只能选一项"}},
            "subquestions": {"sq-m1": {"text": "甲（中文）"}},
            "answers": [
                {"question": "q-single", "code": "A1", "text": "甲（中文）"},
                {"question": "q-single", "code": "A2", "scale": 0, "text": "乙（中文）"},
            ],
        }
    }


def branded_payload(branding=None, translations=None, languages=("zh-Hans",), theme=BRAND_THEME):
    payload = copy.deepcopy(sample_payload())
    payload["theme"] = theme
    payload["additionalLanguages"] = list(languages)
    if branding is not None:
        payload["branding"] = copy.deepcopy(branding)
    if translations is not None:
        payload["translations"] = copy.deepcopy(translations)
    return payload


def branded_definition(branding=None, translations=None, languages=("zh-Hans",), theme=BRAND_THEME):
    return SurveyDefinition.from_dict(branded_payload(branding, translations, languages, theme))
