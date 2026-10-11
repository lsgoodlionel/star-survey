"""品牌 → 引擎主题选项（契约 survey-branding-v1 §2、§5）。

产物是一张**扁平的字符串表**，写进 LSS 的 ``<themes>`` 小节，导入端交给
``TemplateManifest::importManifestLss()`` 落到 ``lime_template_configuration``（按 sid 一行）。

扁平是硬要求：引擎渲染前把每个选项值强转成字符串
（``LSETwigViewRenderer::convertOptionsToArray()``），嵌套对象会变成字面量 ``'N/A'``。
所以按语言的文本用 ``<键>__<语言>`` 这样的平铺键表示，而不是一个 JSON 对象。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..model import SurveyDefinition
from .schema import BrandText, Branding, parse_branding

#: 主题选项名的前缀，和引擎自带选项（brandlogo 等）区分开。
PREFIX = "mjybrand"
#: 按语言覆盖的分隔符。`-` 在语言代码里出现（zh-Hans），所以用双下划线。
LANGUAGE_SEPARATOR = "__"

ASSET_DIRECTORY = "./files/"


@dataclass(frozen=True)
class CompiledBranding:
    """一次品牌编译的产物：主题名 ＋ 按问卷的主题选项。"""

    theme_name: str
    options: Dict[str, str]
    engine_branding: Dict[str, Any]


def compile_branding(definition: SurveyDefinition) -> Optional[CompiledBranding]:
    """定义没有 ``branding`` 返回 None。调用方保证定义已通过校验。"""
    branding = parse_branding(definition)
    if branding is None:
        return None
    return CompiledBranding(
        theme_name=definition.theme,
        options=_options(branding),
        engine_branding=_engine_branding(branding),
    )


def _options(branding: Branding) -> Dict[str, str]:
    options: Dict[str, str] = {}
    if branding.primary_color:
        options[PREFIX + "primary"] = branding.primary_color
    logo_file = branding.logo_file or _materialized_filename(branding.logo_asset)
    favicon_file = branding.favicon_file or _materialized_filename(branding.favicon_asset)
    if (branding.logo_asset and not branding.logo_asset.filename) or (
        branding.favicon_asset and not branding.favicon_asset.filename
    ):
        raise ValueError("brand asset reference requires trusted platform MIME and size metadata")
    if logo_file:
        options[PREFIX + "logo"] = "on"
        options[PREFIX + "logofile"] = ASSET_DIRECTORY + logo_file
    if branding.logo_alt:
        options[PREFIX + "logoalt"] = branding.logo_alt
    if favicon_file:
        options[PREFIX + "favicon"] = ASSET_DIRECTORY + favicon_file
    options.update(_text_options(PREFIX + "title", branding.page_title))
    options.update(_text_options(PREFIX + "footer", branding.footer_text))
    return options


def _materialized_filename(asset) -> str:
    return asset.filename if asset is not None else ""


def _engine_branding(branding: Branding) -> Dict[str, Any]:
    """返回只含引擎可消费字段的品牌定义，不泄漏作者资产引用或取件元数据。"""
    result: Dict[str, Any] = {"brandingVersion": 1}
    if branding.primary_color:
        result["primaryColor"] = branding.primary_color
    if not branding.page_title.is_empty:
        result["pageTitle"] = dict(branding.page_title.by_language) if branding.page_title.by_language else branding.page_title.default
    if not branding.footer_text.is_empty:
        result["footerText"] = dict(branding.footer_text.by_language) if branding.footer_text.by_language else branding.footer_text.default
    logo_file = branding.logo_file or _materialized_filename(branding.logo_asset)
    favicon_file = branding.favicon_file or _materialized_filename(branding.favicon_asset)
    if logo_file:
        result["logoFile"] = logo_file
    if branding.logo_alt:
        result["logoAlt"] = branding.logo_alt
    if favicon_file:
        result["faviconFile"] = favicon_file
    return result


def _text_options(key: str, text: BrandText) -> Dict[str, str]:
    """默认值一个键，每个语言再一个键；主题按当前语言取，取不到用默认值。"""
    if text.is_empty:
        return {}
    options = {}
    if text.default:
        options[key] = text.default
    for language, value in sorted(text.by_language.items()):
        if value:
            options[key + LANGUAGE_SEPARATOR + language] = value
    return options
