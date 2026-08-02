import unittest

from guoling_task_ocr.app import is_monster_task_target, search_official_monsters


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

    def test_detects_explicit_monster_objectives(self) -> None:
        self.assertTrue(is_monster_task_target(["消灭怪物：马匪 0/5"], "马匪"))

    def test_detects_monster_objective_by_official_name(self) -> None:
        self.assertTrue(is_monster_task_target(["击败竹叶青 0/5"], "竹叶青"))

    def test_keeps_item_objectives_eligible_for_market_queries(self) -> None:
        self.assertFalse(is_monster_task_target(["消耗物品：马匪令牌 0/5"], "马匪令牌"))

    def test_explicit_item_target_wins_over_nearby_monster_keywords(self) -> None:
        self.assertFalse(
            is_monster_task_target(
                ["消灭怪物", "道具：四阶元神升阶石 0/1"],
                "四阶元神升阶石",
                "item",
            )
        )


if __name__ == "__main__":
    unittest.main()
