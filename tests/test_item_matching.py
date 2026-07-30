import unittest
from unittest.mock import patch

from guoling_task_ocr.app import match_official_item_name


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


if __name__ == "__main__":
    unittest.main()
