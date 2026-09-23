"""排序题名次解析的零件测试（pubgw/ranking.py）。

端到端语义在 tests/test_responses.py 的 RankingColumnTest；这里只钉住解析、投影与缓存边界。
"""

import unittest

from pubgw.ranking import (
    RankingColumns,
    RankingLayoutCache,
    UntrustedRanking,
    decompose,
    missing_main_columns,
    ranked_items,
    ranking_columns,
    requested_columns,
)

MAIN, R1, R2 = "Q7", "Q7_S11", "Q7_S12"


def entry(fieldname, qid, type_, aid=None):
    row = {"fieldname": fieldname, "qid": qid, "type": type_}
    if aid is not None:
        row["aid"] = aid
    return row


class RankingColumnsTest(unittest.TestCase):
    def test_ranks_come_out_in_rank_order_whatever_the_fieldmap_order(self):
        raw = {
            R2: entry(R2, 7, "R", 2),
            MAIN: entry(MAIN, 7, "R"),
            R1: entry(R1, 7, "R", 1),
        }

        self.assertEqual((RankingColumns(MAIN, (R1, R2)),), ranking_columns(raw))

    def test_other_question_types_and_metadata_rows_are_ignored(self):
        raw = {
            "id": {"fieldname": "id", "type": "id"},
            "token": {"fieldname": "token", "type": ""},
            "Q1": entry("Q1", 1, "S"),
            "Q2_S3": entry("Q2_S3", 2, "M", ""),
            MAIN: entry(MAIN, 7, "R"),
            R1: entry(R1, 7, "R", 1),
        }

        self.assertEqual((RankingColumns(MAIN, (R1,)),), ranking_columns(raw))

    def test_a_ranking_without_a_main_column_is_dropped_rather_than_guessed(self):
        self.assertEqual((), ranking_columns({R1: entry(R1, 7, "R", 1)}))

    def test_two_rankings_are_kept_apart_by_qid(self):
        raw = {
            MAIN: entry(MAIN, 7, "R"),
            R1: entry(R1, 7, "R", 1),
            "Q9": entry("Q9", 9, "R"),
            "Q9_S20": entry("Q9_S20", 9, "R", 1),
        }

        self.assertEqual(
            (RankingColumns(MAIN, (R1,)), RankingColumns("Q9", ("Q9_S20",))), ranking_columns(raw)
        )


class RankedItemsTest(unittest.TestCase):
    def test_an_absent_value_means_the_question_does_not_apply(self):
        self.assertIsNone(ranked_items(None))

    def test_an_empty_string_reads_as_nothing_ranked(self):
        self.assertEqual((), ranked_items(""))

    def test_numeric_item_codes_become_text(self):
        self.assertEqual(("3", "1"), ranked_items("[3, 1]"))

    def test_anything_that_is_not_an_array_of_codes_is_refused(self):
        for value in ("not json", '{"a":1}', "[[1]]", "[null]", "[true]", '[{"a":1}]'):
            with self.subTest(value=value):
                with self.assertRaises(UntrustedRanking):
                    ranked_items(value)


class ProjectionTest(unittest.TestCase):
    LAYOUTS = (RankingColumns(MAIN, (R1, R2)),)

    def test_only_rankings_whose_ranks_were_asked_for_need_decomposing(self):
        self.assertEqual((), requested_columns(self.LAYOUTS, [MAIN]))
        self.assertEqual(self.LAYOUTS, requested_columns(self.LAYOUTS, [R2]))

    def test_the_main_column_is_added_only_when_it_was_not_asked_for(self):
        self.assertEqual((MAIN,), missing_main_columns(self.LAYOUTS, [R1]))
        self.assertEqual((), missing_main_columns(self.LAYOUTS, [MAIN, R1]))

    def test_decompose_leaves_unrelated_columns_alone(self):
        row = {MAIN: '["b"]', R1: None, "Q1": "hello"}

        self.assertEqual({MAIN: '["b"]', R1: "b", "Q1": "hello"}, decompose(row, self.LAYOUTS))

    def test_decompose_does_not_mutate_the_row_it_was_given(self):
        row = {MAIN: '["b"]', R1: None}

        decompose(row, self.LAYOUTS)

        self.assertEqual({MAIN: '["b"]', R1: None}, row)


class LayoutCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.loads = 0

    def load(self):
        self.loads += 1
        return (RankingColumns(MAIN, (R1,)),)

    def cache(self, ttl=300.0, capacity=2):
        return RankingLayoutCache(lambda: self.clock[0], ttl=ttl, capacity=capacity)

    def test_a_second_read_of_the_same_survey_does_not_ask_the_engine_again(self):
        cache = self.cache()

        self.assertEqual(cache.get(("e", 1), self.load), cache.get(("e", 1), self.load))
        self.assertEqual(1, self.loads)

    def test_the_same_sid_on_another_instance_is_a_different_entry(self):
        cache = self.cache()

        cache.get(("e", 1), self.load)
        cache.get(("other", 1), self.load)

        self.assertEqual(2, self.loads)

    def test_entries_expire_so_a_repaired_survey_is_re_read(self):
        cache = self.cache(ttl=60.0)

        cache.get(("e", 1), self.load)
        self.clock[0] += 61.0
        cache.get(("e", 1), self.load)

        self.assertEqual(2, self.loads)

    def test_memory_is_bounded_by_evicting_the_least_recently_used_survey(self):
        cache = self.cache(capacity=2)

        for sid in range(1, 6):
            cache.get(("e", sid), self.load)

        self.assertEqual(5, self.loads)
        self.assertEqual(2, len(cache._entries))  # noqa: SLF001 — 上限是本测试的被测事实


if __name__ == "__main__":
    unittest.main()
