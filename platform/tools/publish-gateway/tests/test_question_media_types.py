"""WP-02 切片 02.6：R02-20 轮播图——第一条接上平台资产服务的题型。

和之前几类最大的不同在**图从哪来**：作者在草稿里只写 ``assetId``，平台发布时把它换成
签名取件地址（ADR 0019 决定 5），网关看到的永远是 ``url`` ＋ ``assetVersion``。
所以这里校验的重点是「**作者自己填的地址进不来**」——正是资产服务要取代的临时做法
（热力图底图、货架图、PK 参赛图至今还是作者填的 URL）。

数据形状就是原生单选：一张幻灯片对一个选项，答案代码即选项代码，不需要副表。
"""

import json
import unittest

from pubgw.questions.theme_kit import CAROUSEL_AUTOPLAY_ATTRIBUTE, CAROUSEL_SLIDES_ATTRIBUTE

from .theme_fixtures import carousel, codes, compile_attributes, slide


class CarouselSlidesTest(unittest.TestCase):

    def attributes(self, **options):
        return compile_attributes(carousel(**options), code="QCARO")

    def slides(self, **options):
        return json.loads(self.attributes(**options)[CAROUSEL_SLIDES_ATTRIBUTE])

    # ---- 幻灯片与选项一一对应 ----

    def test_a_slide_per_option_is_accepted(self):
        self.assertEqual([], codes(carousel()))

    def test_the_slides_must_cover_every_option_exactly(self):
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[slide("A1")])))
        self.assertIn("E_THEME_OPTION_VALUE",
                      codes(carousel(slides=[slide("A1"), slide("A2"), slide("A9")])))

    def test_a_slide_may_not_repeat_an_option(self):
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[slide("A1"), slide("A1")])))

    def test_slides_are_required(self):
        self.assertIn("E_THEME_OPTION_REQUIRED", codes(carousel(slides=None)))
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[])))

    # ---- 替代文本：无障碍是 R02-20 的验收项，不是可选装饰 ----

    def test_every_slide_needs_alternative_text(self):
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[slide("A1", alt=""), slide("A2")])))

    def test_the_alternative_text_survives_into_the_attribute(self):
        self.assertEqual(["包装甲", "包装乙"], [item["alt"] for item in self.slides()])

    # ---- 地址：只接受平台签发的形状 ----

    def test_a_slide_without_an_address_is_refused(self):
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[slide("A1", url=None), slide("A2")])))

    def test_a_javascript_address_is_refused(self):
        self.assertIn("E_THEME_OPTION_VALUE",
                      codes(carousel(slides=[slide("A1", url="javascript:alert(1)"), slide("A2")])))

    def test_a_data_address_is_refused(self):
        self.assertIn("E_THEME_OPTION_VALUE",
                      codes(carousel(slides=[slide("A1", url="data:text/html;base64,PHN2Zz4="), slide("A2")])))

    def test_a_protocol_relative_address_is_refused(self):
        self.assertIn("E_THEME_OPTION_VALUE",
                      codes(carousel(slides=[slide("A1", url="//evil.example/x.png"), slide("A2")])))

    def test_an_unresolved_asset_reference_is_refused(self):
        """定义里还留着 assetId 说明平台没有物化——发出去图会全裂，宁可 422。"""
        unresolved = slide("A1")
        unresolved["assetId"] = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[unresolved, slide("A2")])))

    def test_the_pinned_asset_version_is_required(self):
        self.assertIn("E_THEME_OPTION_VALUE", codes(carousel(slides=[slide("A1", version=None), slide("A2")])))

    def test_the_pinned_version_survives_into_the_attribute(self):
        """版本号留在题目属性里：日后要回答「当时给作答者看的是哪一版图」靠的就是它。"""
        self.assertEqual([1, 1], [item["assetVersion"] for item in self.slides()])

    # ---- 降级 ----

    def test_autoplay_is_off_by_default_and_can_be_turned_on(self):
        self.assertEqual("0", self.attributes()[CAROUSEL_AUTOPLAY_ATTRIBUTE])
        self.assertEqual("1", self.attributes(autoplay=True)[CAROUSEL_AUTOPLAY_ATTRIBUTE])

    def test_the_theme_only_fits_single_choice(self):
        self.assertIn("E_THEME_TYPE_MISMATCH", codes(carousel(qtype="M")))

    def test_the_author_may_not_write_the_managed_attributes_directly(self):
        self.assertIn("E_THEME_ATTRIBUTE_MANAGED",
                      codes(carousel(attributes={CAROUSEL_SLIDES_ATTRIBUTE: "[]"})))
