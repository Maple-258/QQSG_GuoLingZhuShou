import unittest

from guoling_task_ocr.app import search_official_monsters


class MonsterLookupTests(unittest.TestCase):
    MONSTERS = (
        {
            "name": "竹叶青",
            "level": "1",
            "location": "巴郡东郊，巴郡西郊",
            "drops": "小毒囊，青蛇胆",
        },
        {
            "name": "小黑蛇",
            "level": "6~7",
            "location": "疾风岗",
            "drops": "黑蛇胆，毒牙",
        },
    )

    def test_searches_monster_name(self) -> None:
        matches = search_official_monsters("竹叶", self.MONSTERS)
        self.assertEqual([monster["name"] for monster in matches], ["竹叶青"])

    def test_searches_location_and_drops(self) -> None:
        self.assertEqual(len(search_official_monsters("疾风岗", self.MONSTERS)), 1)
        self.assertEqual(search_official_monsters("青蛇胆", self.MONSTERS)[0]["name"], "竹叶青")

    def test_exact_name_is_ranked_first(self) -> None:
        matches = search_official_monsters("小黑蛇", self.MONSTERS)
        self.assertEqual(matches[0]["name"], "小黑蛇")


if __name__ == "__main__":
    unittest.main()
