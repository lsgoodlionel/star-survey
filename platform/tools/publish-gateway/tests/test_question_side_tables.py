"""WP-02 走副表的题型主题：列字典、结构摘要与绑定记录里的副表声明。

作答以 JSON 信封存进基础题型的那一列，真正的结构由插件投影进副表
（platform/contracts/question-extension-tables-v1.md）。枚举列与唯一列是给
多个题型共用的通用列约束，不属于任何一个主题，所以单独一节。
"""

import json
import unittest

from pubgw.fieldmap import binding_map, parse_fieldmap
from pubgw.qtypes import expected_rows
from pubgw.questions.themes import structure_digest

from .fixtures import fieldmap_for
from .qtype_fixtures import definition_with, first_question, question
from .theme_fixtures import (
    LOOP_OBJECTS, PK_ITEMS, PK_PAIRS, SHELF_PRODUCTS, TABLE_COLUMNS, codes, compile_attributes,
    heatmap, image_pk, loop_rating, shelf, table,
)


# ------------------------------------------------------------------ 副表主题


class RepeatingTableTest(unittest.TestCase):
    """R02-13 自增表格：整张表一列 JSON，列定义随 .lss 发布，插件按它投影副表。"""

    def test_columns_and_bounds_compile_to_the_plugin_attributes(self):
        attributes = compile_attributes(table(), code="QTABLE")
        self.assertEqual("rt1", attributes["mjy_structure_version"])
        self.assertEqual("1", attributes["mjy_table_min_rows"])
        self.assertEqual("5", attributes["mjy_table_max_rows"])
        self.assertEqual(TABLE_COLUMNS, json.loads(attributes["mjy_table_columns"]))

    def test_a_column_label_defaults_to_its_code(self):
        value = compile_attributes(table(columns=[{"code": "item"}]), code="QTABLE")["mjy_table_columns"]
        self.assertEqual([{"code": "item", "label": "item", "type": "text", "required": False}], json.loads(value))

    def test_bad_column_definitions(self):
        for columns in ([{"code": "1bad"}],
                        [{"code": "item", "type": "money"}],
                        [{"code": "item"}, {"code": "item"}],
                        [{"code": "item", "maxLength": 0}],
                        [{"code": "item", "min": 1}],
                        [{"code": "qty", "type": "integer", "min": 9, "max": 1}],
                        ["not an object"]):
            with self.subTest(columns=columns):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(columns=columns)))

    def test_too_many_columns(self):
        many = [{"code": "c{}".format(index)} for index in range(41)]
        self.assertIn("E_THEME_OPTION_VALUE", codes(table(columns=many)))

    def test_row_bounds_must_be_ordered_and_within_the_plugin_hard_limit(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(minRows=5, maxRows=2)))
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(maxRows=501)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QTABLE", "", 0),), expected_rows(first_question(table())))


class HeatmapTest(unittest.TestCase):
    """R02-19 热力图选区：归一化坐标，范围由平台写死，插件逐格校验。"""

    def test_generated_columns_pin_the_coordinate_range(self):
        columns = json.loads(compile_attributes(heatmap(), code="QHEAT")["mjy_table_columns"])
        self.assertEqual(["x", "y"], [column["code"] for column in columns])
        for column in columns:
            self.assertEqual(("decimal", True, 0, 1), (column["type"], column["required"], column["min"], column["max"]))

    def test_point_bounds_reach_the_plugin_row_bounds(self):
        attributes = compile_attributes(heatmap(minPoints=1), code="QHEAT")
        self.assertEqual(("1", "3"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_unordered_point_bounds_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(heatmap(minPoints=4, maxPoints=2)))

    def test_the_image_is_required(self):
        payload = heatmap()
        del payload["themeOptions"]["image"]
        self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))


class EnumColumnTest(unittest.TestCase):
    """枚举列与唯一列：给自增表格与循环评价共用的两条列约束。

    两条都必须在服务端成立——浏览器端渲染成下拉还是按钮都不作数，
    提交上来的永远是一个字符串。
    """

    def enum_table(self, column, **options):
        return table(columns=[column], **options)

    def test_an_enum_column_compiles_its_options_canonically(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "required": True,
             "options": [{"code": "A", "label": "优"}, {"code": "B", "label": "良"}]}), code="QTABLE")
        self.assertEqual(
            [{"code": "grade", "label": "grade", "type": "enum", "required": True,
              "options": [{"code": "A", "label": "优"}, {"code": "B", "label": "良"}]}],
            json.loads(value["mjy_table_columns"]),
        )

    def test_a_bare_code_list_is_accepted_and_labelled_by_its_code(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "options": ["A", "B"]}), code="QTABLE")
        self.assertEqual([{"code": "A", "label": "A"}, {"code": "B", "label": "B"}],
                         json.loads(value["mjy_table_columns"])[0]["options"])

    def test_an_enum_column_without_options_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(self.enum_table({"code": "grade", "type": "enum"})))

    def test_options_on_a_non_enum_column_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(self.enum_table({"code": "grade", "type": "text", "options": ["A"]})))

    def test_bad_option_shapes(self):
        # 取值代码可以数字打头（量表常写成 1…5），但不能带空格、也不能是中文。
        for opts in ([], [{"label": "没有代码"}], ["A", "A"], [{"code": "bad code"}], "A,B"):
            with self.subTest(options=opts):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(
                    self.enum_table({"code": "grade", "type": "enum", "options": opts})))

    def test_a_distinct_column_is_carried_through(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "options": ["A"], "distinct": True}), code="QTABLE")
        self.assertTrue(json.loads(value["mjy_table_columns"])[0]["distinct"])

    def test_the_digest_follows_the_options_and_the_distinct_flag(self):
        plain = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "优"}]}]
        relabelled = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "改了标签"}]}]
        widened = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "优"},
                                                             {"code": "B", "label": "良"}]}]
        unique = [dict(plain[0], distinct=True)]
        # 标签不进摘要（改标签不影响读回），取值集合与唯一约束要进。
        self.assertEqual(structure_digest(plain), structure_digest(relabelled))
        self.assertNotEqual(structure_digest(plain), structure_digest(widened))
        self.assertNotEqual(structure_digest(plain), structure_digest(unique))


class LoopRatingTest(unittest.TestCase):
    """R02-11 循环评价：同一套评价维度对每个对象各问一遍，引擎没有循环。

    一行＝一个评价对象，一列＝一个维度；对象列是枚举＋唯一，行数被钉死成对象个数，
    三者合起来逼出「每个对象恰好评一次」，不必另写一套按行下标的规则。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(loop_rating(**options), code="QLOOP")["mjy_table_columns"])

    def test_the_object_column_comes_first_and_is_unique(self):
        first = self.columns()[0]
        self.assertEqual(("target", "enum", True, True), (first["code"], first["type"],
                                                          first["required"], first["distinct"]))
        self.assertEqual(["B1", "B2"], [option["code"] for option in first["options"]])
        self.assertEqual(["甲品牌", "乙品牌"], [option["label"] for option in first["options"]])

    def test_one_enum_column_per_dimension_over_the_declared_scale(self):
        dimensions = self.columns()[1:]
        self.assertEqual(["price", "service"], [column["code"] for column in dimensions])
        for column in dimensions:
            self.assertEqual(("enum", True), (column["type"], column["required"]))
            self.assertEqual(["1", "2", "3"], [option["code"] for option in column["options"]])

    def test_the_row_count_is_pinned_to_the_number_of_objects(self):
        attributes = compile_attributes(loop_rating(), code="QLOOP")
        self.assertEqual(("2", "2"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_object_labels_reach_the_theme(self):
        value = compile_attributes(loop_rating(), code="QLOOP")["mjy_loop_objects"]
        self.assertEqual(LOOP_OBJECTS, json.loads(value))
        self.assertNotIn(" ", value)

    def test_structure_version_is_required(self):
        payload = loop_rating()
        del payload["themeOptions"]["structureVersion"]
        self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_objects_dimensions_and_scale_are_all_required(self):
        for name in ("objects", "dimensions", "scale"):
            with self.subTest(option=name):
                payload = loop_rating()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_bad_object_lists(self):
        for objects in ([{"label": "没有代码"}],
                        [{"code": "B1"}, {"code": "B1"}],
                        [{"code": "bad code"}],
                        [{"code": "B1", "label": 7}]):
            with self.subTest(objects=objects):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(objects=objects)))

    def test_a_dimension_may_not_collide_with_the_object_column(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(loop_rating(dimensions=[{"code": "target", "label": "撞车"}])))

    def test_too_many_objects(self):
        many = [{"code": "B{}".format(index), "label": str(index)} for index in range(501)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(objects=many)))

    def test_too_many_dimensions(self):
        # 对象列占掉一列，所以维度最多 39 个。
        many = [{"code": "d{}".format(index), "label": str(index)} for index in range(40)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(dimensions=many)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QLOOP", "", 0),), expected_rows(first_question(loop_rating())))

    def test_the_theme_is_only_for_long_free_text(self):
        payload = dict(loop_rating(), type="S")
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(payload))


class ImagePkTest(unittest.TestCase):
    """R02-17 图片 PK：成对比较，配对由平台声明，展示位置随作答一并留痕。

    **一对一列**，这一列的可选值恰好是这一对的两张图——所以「选了不在这一对里的东西」
    根本不需要跨列规则，枚举列自己就挡住了。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(image_pk(**options), code="QPK")["mjy_table_columns"])

    def test_each_pair_becomes_a_choice_column_over_its_own_two_items(self):
        chosen = [column for column in self.columns() if not column["code"].endswith("_shown")]
        self.assertEqual(["P1", "P2"], [column["code"] for column in chosen])
        self.assertEqual([["A", "B"], ["B", "C"]],
                         [[option["code"] for option in column["options"]] for column in chosen])
        for column in chosen:
            self.assertEqual(("enum", True), (column["type"], column["required"]))

    def test_each_pair_also_records_which_image_was_shown_first(self):
        """配对随机要可追溯：随机的是展示顺序，那就把它一起记下来。
        这一列不必填——关掉 JavaScript 直接填信封的那条路径给不出展示顺序。"""
        shown = [column for column in self.columns() if column["code"].endswith("_shown")]
        self.assertEqual(["P1_shown", "P2_shown"], [column["code"] for column in shown])
        self.assertEqual([["A", "B"], ["B", "C"]],
                         [[option["code"] for option in column["options"]] for column in shown])
        self.assertEqual([False, False], [column["required"] for column in shown])

    def test_the_answer_is_exactly_one_row(self):
        attributes = compile_attributes(image_pk(), code="QPK")
        self.assertEqual(("1", "1"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_images_reach_the_theme(self):
        value = compile_attributes(image_pk(), code="QPK")["mjy_pk_items"]
        self.assertEqual(PK_ITEMS, json.loads(value))
        self.assertNotIn(" ", value)

    def test_the_pairs_reach_the_theme(self):
        self.assertEqual(PK_PAIRS, json.loads(compile_attributes(image_pk(), code="QPK")["mjy_pk_pairs"]))

    def test_items_pairs_and_structure_version_are_required(self):
        for name in ("structureVersion", "items", "pairs"):
            with self.subTest(option=name):
                payload = image_pk()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_every_item_needs_an_image(self):
        for items in ([{"code": "A", "label": "甲"}],
                      [{"code": "A", "label": "甲", "image": ""}],
                      [{"code": "A", "label": "甲", "image": 7}]):
            with self.subTest(items=items):
                self.assertIn("E_THEME_OPTION_VALUE", codes(image_pk(items=items)))

    def test_a_pair_must_name_two_different_declared_items(self):
        for pairs in ([{"code": "P1", "left": "A", "right": "Z"}],
                      [{"code": "P1", "left": "A", "right": "A"}],
                      [{"code": "P1", "left": "A"}],
                      [{"left": "A", "right": "B"}],
                      [{"code": "P1", "left": "A", "right": "B"}, {"code": "P1", "left": "B", "right": "C"}]):
            with self.subTest(pairs=pairs):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(image_pk(pairs=pairs)))

    def test_a_pair_code_leaves_room_for_the_shown_suffix(self):
        long_code = "P" * 30
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(image_pk(pairs=[{"code": long_code, "left": "A", "right": "B"}])))

    def test_too_many_pairs(self):
        # 一对占两列，列数上限 40，所以最多 20 对。
        many = [{"code": "P{}".format(index), "left": "A", "right": "B"} for index in range(21)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(image_pk(pairs=many)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QPK", "", 0),), expected_rows(first_question(image_pk())))


class ShelfTest(unittest.TestCase):
    """R02-18 货架题：在货架图上按热区取货，取了什么、取了几件。

    商品列是枚举＋唯一（同一件商品不能取两次，要多拿就改数量），
    数量列是整数带上下限。货架版本就是结构版本——换了货架图或挪了热区就得换一版，
    否则半年前的答卷会被按今天的货架解释。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(shelf(**options), code="QSHELF")["mjy_table_columns"])

    def test_the_product_column_is_a_unique_enum_over_the_declared_products(self):
        product = self.columns()[0]
        self.assertEqual(("product", "enum", True, True),
                         (product["code"], product["type"], product["required"], product["distinct"]))
        self.assertEqual(["S1", "S2"], [option["code"] for option in product["options"]])

    def test_the_quantity_column_is_a_bounded_integer(self):
        quantity = self.columns()[1]
        self.assertEqual(("qty", "integer", True, 1, 99),
                         (quantity["code"], quantity["type"], quantity["required"],
                          quantity["min"], quantity["max"]))

    def test_the_quantity_cap_is_configurable(self):
        self.assertEqual(5, self.columns(maxQuantity=5)[1]["max"])

    def test_pick_bounds_reach_the_plugin_row_bounds(self):
        attributes = compile_attributes(shelf(minPicks=1, maxPicks=4), code="QSHELF")
        self.assertEqual(("1", "4"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_unordered_pick_bounds_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(shelf(minPicks=4, maxPicks=2)))

    def test_you_cannot_be_asked_for_more_picks_than_there_are_products(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(shelf(minPicks=3, maxPicks=3)))

    def test_the_shelf_photo_and_the_hotspots_reach_the_theme(self):
        attributes = compile_attributes(shelf(), code="QSHELF")
        self.assertEqual("https://assets.example.invalid/shelf.png", attributes["mjy_shelf_image"])
        self.assertEqual(SHELF_PRODUCTS, json.loads(attributes["mjy_shelf_products"]))

    def test_structure_version_image_and_products_are_required(self):
        for name in ("structureVersion", "image", "products"):
            with self.subTest(option=name):
                payload = shelf()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_hotspots_are_normalised_coordinates(self):
        """热区一律归一化到 [0,1]：货架图换了尺寸，坐标不用跟着改。"""
        for product in ({"code": "S1", "label": "牛奶", "x": -0.1, "y": 0.2, "w": 0.2, "h": 0.2},
                        {"code": "S1", "label": "牛奶", "x": 0.1, "y": 1.2, "w": 0.2, "h": 0.2},
                        {"code": "S1", "label": "牛奶", "x": 0.1, "y": 0.2, "w": 0, "h": 0.2},
                        {"code": "S1", "label": "牛奶", "x": 0.9, "y": 0.2, "w": 0.5, "h": 0.2},
                        {"code": "S1", "label": "牛奶", "x": 0.1, "y": 0.2},
                        {"code": "S1", "label": "牛奶", "x": "左", "y": 0.2, "w": 0.2, "h": 0.2}):
            with self.subTest(product=product):
                self.assertIn("E_THEME_OPTION_VALUE", codes(shelf(products=[product])))

    def test_duplicate_product_codes_are_rejected(self):
        twice = [SHELF_PRODUCTS[0], dict(SHELF_PRODUCTS[1], code="S1")]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(shelf(products=twice)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QSHELF", "", 0),), expected_rows(first_question(shelf())))


class SideTableBindingTest(unittest.TestCase):
    """绑定记录里的副表声明：读端据此解释单元格（contracts/question-extension-tables-v1.md）。"""

    def binding(self, *questions, code):
        definition = definition_with(*questions)
        rows = parse_fieldmap(fieldmap_for(definition))
        return {item.code: item for item in binding_map(definition, rows)}[code]

    def test_a_plain_question_declares_no_side_table(self):
        item = self.binding(question("S", code="QS"), code="QS")
        self.assertIsNone(item.side_table)
        self.assertNotIn("sideTable", item.to_dict())

    def test_a_repeating_table_declares_its_columns_and_structure_version(self):
        declared = self.binding(table(), code="QTABLE").to_dict()["sideTable"]
        self.assertEqual("question-extension-tables-v1", declared["contract"])
        self.assertEqual("mjyquestionextensions_answer_cell", declared["cellTable"])
        self.assertEqual("mjyquestionextensions_answer_state", declared["stateTable"])
        self.assertEqual("rt1", declared["structureVersion"])
        self.assertEqual(TABLE_COLUMNS, declared["columns"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_a_heatmap_declares_the_generated_columns(self):
        declared = self.binding(heatmap(), code="QHEAT").to_dict()["sideTable"]
        self.assertEqual(["x", "y"], [column["code"] for column in declared["columns"]])
        self.assertEqual("hm1", declared["structureVersion"])

    def test_a_shelf_declares_the_product_and_quantity_columns(self):
        declared = self.binding(shelf(), code="QSHELF").to_dict()["sideTable"]
        self.assertEqual(["product", "qty"], [column["code"] for column in declared["columns"]])
        self.assertEqual("sh1", declared["structureVersion"])

    def test_an_image_pk_declares_one_column_per_pair_plus_its_shown_column(self):
        declared = self.binding(image_pk(), code="QPK").to_dict()["sideTable"]
        self.assertEqual(["P1", "P1_shown", "P2", "P2_shown"],
                         [column["code"] for column in declared["columns"]])
        self.assertEqual("pk1", declared["structureVersion"])

    def test_a_loop_rating_declares_the_object_column_and_every_dimension(self):
        declared = self.binding(loop_rating(), code="QLOOP").to_dict()["sideTable"]
        self.assertEqual(["target", "price", "service"], [column["code"] for column in declared["columns"]])
        self.assertEqual("lr1", declared["structureVersion"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_the_digest_follows_the_structure_and_ignores_labels(self):
        relabelled = [dict(column, label="改了标签") for column in TABLE_COLUMNS]
        retyped = [dict(TABLE_COLUMNS[0]), dict(TABLE_COLUMNS[1], type="decimal")]
        self.assertEqual(structure_digest(TABLE_COLUMNS), structure_digest(relabelled))
        self.assertNotEqual(structure_digest(TABLE_COLUMNS), structure_digest(retyped))

    def test_the_binding_record_round_trips_the_side_table(self):
        from pubgw.binding import BindingRecord

        item = self.binding(table(), code="QTABLE")
        record = BindingRecord("e1", 1, "d", "c", "fm1", "fm1:x", "en", "now", (item,))
        restored = BindingRecord.from_dict(record.to_dict())
        self.assertEqual(item.side_table, restored.questions[0].side_table)
