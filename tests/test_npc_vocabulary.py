import unittest

from guoling_task_ocr.app import correct_npc_name, correct_role_name, load_official_npc_names


class NpcVocabularyTests(unittest.TestCase):
    def test_bundled_official_npc_vocabulary_contains_qintiangjian(self) -> None:
        self.assertIn("钦天监", load_official_npc_names())

    def test_role_alias_corrects_the_confirmed_english_name(self) -> None:
        self.assertEqual(correct_role_name("Haplescx"), "Maplescx")

    def test_npc_one_character_ocr_correction_requires_unique_match(self) -> None:
        self.assertEqual(correct_npc_name("钦天间"), "钦天监")


if __name__ == "__main__":
    unittest.main()
