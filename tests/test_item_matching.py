import unittest
from unittest.mock import patch

from guoling_task_ocr.app import correct_item_name, match_official_item_name, normalize_market_keyword


class ItemNameMatchingTests(unittest.TestCase):
    def test_corrects_unique_first_character_ocr_error(self) -> None:
        with patch(
            "guoling_task_ocr.app.load_official_item_names",
            return_value=frozenset({"僵尸大白菜"}),
        ):
            self.assertEqual(match_official_item_name("遥尸大白菜"), "僵尸大白菜")

    def test_keeps_candidate_when_nearest_match_is_ambiguous(self) -> None:
        with patch(
            "guoling_task_ocr.app.load_official_item_names",
            return_value=frozenset({"僵尸大白菜", "遥尸大白菜"}),
        ):
            self.assertEqual(match_official_item_name("疆尸大白菜"), "疆尸大白菜")

    def test_does_not_change_an_item_quality_prefix_to_match_the_vocabulary(self) -> None:
        with patch(
            "guoling_task_ocr.app.load_official_item_names",
            return_value=frozenset({"中品复原灵石"}),
        ):
            self.assertEqual(match_official_item_name("下品复原灵石"), "下品复原灵石")

    def test_corrects_a_unique_two_character_error_in_a_long_item_name(self) -> None:
        with patch(
            "guoling_task_ocr.app.load_all_item_names",
            return_value=frozenset({"四阶元神升阶石"}),
        ):
            self.assertEqual(match_official_item_name("四阶元神开阶右"), "四阶元神升阶石")

    def test_corrects_a_unique_missing_character_in_an_item_name(self) -> None:
        with patch(
            "guoling_task_ocr.app.load_all_item_names",
            return_value=frozenset({"灵魄成长石"}),
        ):
            self.assertEqual(match_official_item_name("灵魄成石"), "灵魄成长石")

    def test_normalizes_full_width_parentheses_for_market_and_item_matching(self) -> None:
        self.assertEqual(normalize_market_keyword("气旋秘籍（高）"), "气旋秘籍(高)")
        self.assertEqual(correct_item_name("气旋秘籍（高）"), "气旋秘籍(高)")


if __name__ == "__main__":
    unittest.main()
