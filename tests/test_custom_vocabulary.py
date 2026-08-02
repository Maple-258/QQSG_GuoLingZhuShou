import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guoling_task_ocr.app import (
    correct_item_name,
    learn_market_item_name,
    load_custom_item_vocabulary,
    match_official_item_name,
    save_custom_item_vocabulary,
)


class CustomVocabularyTests(unittest.TestCase):
    def test_saved_names_and_aliases_are_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            vocabulary_path = Path(temporary_directory) / "custom_item_vocabulary.json"
            save_custom_item_vocabulary(
                {"自定义灵药"},
                {"自订灵药": "自定义灵药"},
                vocabulary_path,
            )
            names, aliases = load_custom_item_vocabulary(vocabulary_path)
            self.assertEqual(names, frozenset({"自定义灵药"}))
            self.assertEqual(aliases, {"自订灵药": "自定义灵药"})

    def test_custom_name_participates_in_unique_character_matching(self) -> None:
        with patch("guoling_task_ocr.app.load_all_item_names", return_value=frozenset({"自定义灵药"})):
            self.assertEqual(match_official_item_name("自订义灵药"), "自定义灵药")

    def test_custom_alias_overrides_builtin_alias(self) -> None:
        with patch("guoling_task_ocr.app.load_item_aliases", return_value={"误识名": "内置名称"}), patch(
            "guoling_task_ocr.app.load_custom_item_vocabulary",
            return_value=(frozenset({"自定义名称"}), {"误识名": "自定义名称"}),
        ):
            self.assertEqual(correct_item_name("误识名"), "自定义名称")

    def test_unique_market_item_name_is_saved_as_a_local_ocr_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            vocabulary_path = Path(temporary_directory) / "custom_item_vocabulary.json"
            learned = learn_market_item_name(
                "元神秘藉书",
                [{"name": "元神秘籍书"}, {"name": "元神秘籍书"}],
                vocabulary_path,
            )
            names, aliases = load_custom_item_vocabulary(vocabulary_path)

        self.assertEqual(learned, "元神秘籍书")
        self.assertIn("元神秘籍书", names)
        self.assertEqual(aliases["元神秘藉书"], "元神秘籍书")


if __name__ == "__main__":
    unittest.main()
