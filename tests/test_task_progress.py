import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from guoling_task_ocr.task_progress import (
    ParsedTaskProgress,
    TaskObjective,
    find_role_name,
    filter_task_progress_records,
    infer_unread_task_step,
    is_role_name_candidate,
    load_task_progress,
    parse_task_progress,
    parse_task_objective,
    record_task_progress,
    save_task_progress,
    summarize_task_rounds,
    summarize_latest_task_rounds,
    task_record_key,
)


class TaskProgressTests(unittest.TestCase):
    def test_parses_a_numbered_step_task_with_role_and_progress(self) -> None:
        parsed = parse_task_progress(["角色：小乔", "200步任务", "当前进度：37 / 200"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.role, "小乔")
        self.assertEqual(parsed.task, "修行之途")
        self.assertEqual((parsed.current_step, parsed.total_steps), (37, 200))

    def test_parses_yuanshen_practice_as_a_multistep_task(self) -> None:
        parsed = parse_task_progress(["角色名：青竹", "元神修行", "第 6 步", "进度 6/12"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.task, "元神修行")
        self.assertEqual((parsed.current_step, parsed.total_steps), (6, 12))

    def test_uses_step_number_when_the_total_is_not_visible(self) -> None:
        parsed = parse_task_progress(["元神修行", "第 4 步"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.current_step, parsed.total_steps), (4, 0))

    def test_parses_advanced_guoling_step_displayed_after_the_task_name(self) -> None:
        parsed = parse_task_progress(["高级国令慕贤", "1", "物品：马匪"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.task, "高级国令慕贤")
        self.assertEqual((parsed.current_step, parsed.total_steps), (1, 0))

    def test_finds_role_name_from_upper_left_hud_ocr(self) -> None:
        self.assertEqual(find_role_name(["LV150", "镶苔的来路", "生命", "235751/235751"], allow_unlabelled=True), "镶苔的来路")
        self.assertEqual(find_role_name(["LV120 Maplescx", "生命", "75388/75388"], allow_unlabelled=True), "Maplescx")
        self.assertFalse(is_role_name_candidate("Sv120"))

    def test_parses_monster_target_and_required_count(self) -> None:
        objective = parse_task_objective(["NPC:庞德", "消灭怪物:马匪 0/5"])

        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual((objective.kind, objective.name, objective.display_quantity, objective.npc), ("monster", "马匪", "需要 5 个（当前 0）", "庞德"))

    def test_tolerates_a_missing_first_character_in_monster_action_and_split_npc(self) -> None:
        objective = parse_task_objective(["NPC:", "庞德", "灭怪物:马匪0/5"])

        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual((objective.kind, objective.name, objective.display_quantity, objective.npc), ("monster", "马匪", "需要 5 个（当前 0）", "庞德"))

    def test_uses_npc_as_target_when_no_item_or_monster_is_required(self) -> None:
        objective = parse_task_objective(["NPC：庞德", "请前往交谈"])

        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual((objective.kind, objective.name, objective.display_quantity), ("npc", "庞德", "-"))

    def test_parses_a_labelled_item_objective_before_falling_back_to_npc(self) -> None:
        objective = parse_task_objective(["NPC:钦天监", "道具:四阶元神升阶石0/1"])

        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual(
            (objective.kind, objective.name, objective.display_quantity, objective.npc),
            ("item", "四阶元神升阶石", "需要 1 个（当前 0）", "钦天监"),
        )

    def test_parses_an_item_target_containing_parentheses_before_falling_back_to_npc(self) -> None:
        objective = parse_task_objective(["NPC:刘备", "道具:气旋秘籍(高)0/1"])

        self.assertIsNotNone(objective)
        assert objective is not None
        self.assertEqual(
            (objective.kind, objective.name, objective.current_count, objective.required_count, objective.npc),
            ("item", "气旋秘籍(高)", 0, 1, "刘备"),
        )

    def test_parses_an_inline_advanced_guoling_step_and_ocr_variant(self) -> None:
        parsed = parse_task_progress(["高级国令募贤2", "道具:四阶元神升阶石0/1"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.task, parsed.current_step, parsed.total_steps), ("高级国令慕贤", 2, 0))

    def test_parses_the_duo_character_ocr_variant_for_advanced_guoling(self) -> None:
        parsed = parse_task_progress(["高级国令夺贤 1", "物品"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.task, parsed.current_step, parsed.total_steps), ("高级国令慕贤", 1, 0))

    def test_repairs_a_spurious_leading_one_in_an_eight_step_guoling_task(self) -> None:
        parsed = parse_task_progress(["高级国令慕贤12"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.current_step, parsed.total_steps), (2, 0))

    def test_prefers_labelled_progress_over_a_task_title_step_misread(self) -> None:
        parsed = parse_task_progress(["元神修行 第12步", "当前进度 2/30"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.current_step, parsed.total_steps), (2, 30))

    def test_rejects_steps_past_known_task_limits(self) -> None:
        self.assertIsNone(parse_task_progress(["高级国令慕贤 第9步"]))
        self.assertIsNone(parse_task_progress(["元神修行 第31步"]))

    def test_does_not_use_an_item_requirement_as_the_task_step(self) -> None:
        parsed = parse_task_progress(["高级国令慕贤 6", "道具:下品复原灵石 当前0/40"])

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed.task, parsed.current_step, parsed.total_steps), ("高级国令慕贤", 6, 0))

    def test_records_are_persisted_by_role_and_task(self) -> None:
        parsed = parse_task_progress(["200步任务", "进度 37/200"])
        assert parsed is not None
        records = {}
        objective = parse_task_objective(["道具:四阶元神升阶石0/1"])
        assert objective is not None
        record_task_progress(records, "小乔", parsed, objective, 1234)
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "task_progress.json"
            save_task_progress(records, path)
            restored = load_task_progress(path)

        record = next(record for record in restored.values() if record.role == "小乔" and record.task == "修行之途")
        self.assertEqual(record.display_progress, "37 / 200")
        self.assertEqual(record.display_quantity, "1 个")
        self.assertEqual(record.display_cost, "1,234 x 1 = 1,234")

    def test_keeps_each_step_and_persists_monster_objectives(self) -> None:
        records = {}
        item_step = ParsedTaskProgress("高级国令慕贤", 2, 0)
        monster_step = ParsedTaskProgress("高级国令慕贤", 3, 0)
        item = TaskObjective("item", "四阶元神升阶石", 0, 1)
        monster = TaskObjective("monster", "白孔雀", 0, 5)

        record_task_progress(records, "小乔", item_step, item, 1200)
        monster_record = record_task_progress(records, "小乔", monster_step, monster)
        self.assertEqual(len(records), 2)
        self.assertEqual(monster_record.display_objective, "怪物：白孔雀")
        self.assertEqual(monster_record.display_quantity, "5 个")
        self.assertEqual(monster_record.display_cost, "-")

        # Re-observing the same step refreshes that row without replacing history.
        record_task_progress(records, "小乔", monster_step, TaskObjective("monster", "白孔雀", 2, 5))
        self.assertEqual(len(records), 2)

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "task_progress.json"
            save_task_progress(records, path)
            restored = load_task_progress(path)

        self.assertEqual(len(restored), 2)
        self.assertTrue(any(record.current_step == 2 for record in restored.values()))
        self.assertTrue(any(record.current_step == 3 and record.objective_kind == "monster" for record in restored.values()))

    def test_summarizes_completed_round_cost_and_starts_a_new_round(self) -> None:
        records = {}
        objective = TaskObjective("item", "四阶元神升阶石", 0, 1)
        for step in range(1, 9):
            record_task_progress(records, "小乔", ParsedTaskProgress("高级国令慕贤", step, 0), objective, 100)

        first_summary = summarize_latest_task_rounds(records, "小乔")
        self.assertEqual(len(first_summary), 1)
        self.assertEqual(
            (first_summary[0].round_index, first_summary[0].recorded_steps, first_summary[0].total_steps, first_summary[0].total_cost),
            (1, 8, 8, 800),
        )

        record_task_progress(records, "小乔", ParsedTaskProgress("高级国令慕贤", 1, 0), objective, 150)
        second_summary = summarize_latest_task_rounds(records, "小乔")
        self.assertEqual(len(records), 9)
        self.assertEqual(
            (second_summary[0].round_index, second_summary[0].recorded_steps, second_summary[0].total_steps, second_summary[0].total_cost),
            (2, 1, 8, 150),
        )
        all_rounds = summarize_task_rounds(records, "小乔")
        self.assertEqual(
            [(summary.round_index, summary.total_cost) for summary in all_rounds],
            [(1, 800), (2, 150)],
        )

    def test_starts_a_new_round_after_missed_intermediate_steps(self) -> None:
        records = {}
        objective = TaskObjective("item", "灵魄成长石", 0, 1)
        for step in (1, 3, 6):
            record_task_progress(records, "小乔", ParsedTaskProgress("高级国令慕贤", step, 8), objective)

        new_round = record_task_progress(
            records, "小乔", ParsedTaskProgress("高级国令慕贤", 1, 8), objective
        )
        self.assertEqual(new_round.round_index, 2)
        self.assertEqual(len(records), 4)

    def test_repeated_first_step_does_not_start_a_new_round(self) -> None:
        records = {}
        objective = TaskObjective("item", "灵魄成长石", 0, 1)
        record_task_progress(records, "小乔", ParsedTaskProgress("修行之途", 1, 200), objective)
        repeated = record_task_progress(records, "小乔", ParsedTaskProgress("修行之途", 1, 200), objective)
        self.assertEqual(repeated.round_index, 1)
        self.assertEqual(len(records), 1)

    def test_inferring_an_unread_step_uses_new_target_or_reuses_matching_target(self) -> None:
        records = {}
        for step, name in ((1, "圣灵珠"), (2, "次级翻新灵石"), (4, "遗毒蜘蛛"), (5, "蝶妖")):
            kind = "monster" if step >= 4 else "item"
            record_task_progress(
                records,
                "ZGP",
                ParsedTaskProgress("高级国令慕贤", step, 0),
                TaskObjective(kind, name, 0, 1),
            )

        current = TaskObjective("item", "灵魄成长石", 1, 1)
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", current), 6)
        repeated = TaskObjective("monster", "蝶妖", 0, 1)
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", repeated), 5)

    def test_inferring_an_unread_step_tolerates_a_small_target_ocr_error(self) -> None:
        records = {}
        record_task_progress(
            records,
            "ZGP",
            ParsedTaskProgress("高级国令慕贤", 3, 0),
            TaskObjective("item", "灵魄成长石", 0, 1),
        )
        mistyped = TaskObjective("item", "灵魂成长石", 0, 1)
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", mistyped), 3)

    def test_inferring_an_unread_step_records_an_npc_after_item_steps(self) -> None:
        records = {}
        for step, name in ((1, "九级嵌器圣石"), (2, "霸王精华级"), (3, "人阶强化灵宝"), (4, "尊品装备打造模")):
            record_task_progress(
                records,
                "ZGP",
                ParsedTaskProgress("高级国令慕贤", step, 0),
                TaskObjective("item", name, 0, 1),
            )

        npc = TaskObjective("npc", "魏延", npc="魏延")
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", npc), 5)
        record_task_progress(records, "ZGP", ParsedTaskProgress("高级国令慕贤", 5, 0), npc)
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", npc), 5)

    def test_inferred_target_matching_keeps_different_item_grades_separate(self) -> None:
        records = {}
        record_task_progress(
            records,
            "ZGP",
            ParsedTaskProgress("高级国令慕贤", 3, 0),
            TaskObjective("item", "下品复原灵石", 0, 1),
        )
        higher_grade = TaskObjective("item", "中品复原灵石", 0, 1)
        self.assertEqual(infer_unread_task_step(records, "ZGP", "高级国令慕贤", higher_grade), 4)

    def test_records_an_npc_target_without_quantity_or_market_cost(self) -> None:
        records = {}
        record = record_task_progress(
            records,
            "ZGP",
            ParsedTaskProgress("高级国令慕贤", 8, 0),
            TaskObjective("npc", "马良", npc="马良"),
        )

        self.assertEqual(record.display_objective, "NPC：马良")
        self.assertEqual(record.display_quantity, "-")
        self.assertEqual(record.display_cost, "-")

    def test_loads_older_community_task_name_as_xiuxingzhitu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "task_progress.json"
            path.write_text(
                '{"records": [{"role": "小乔", "task": "200步任务", "current_step": 37, '
                '"total_steps": 200, "updated_at": "2026-08-02 09:30"}]}',
                encoding="utf-8",
            )
            records = load_task_progress(path)
        self.assertTrue(any(record.task == "修行之途" for record in records.values()))

    def test_filters_records_by_task_round_and_date(self) -> None:
        records = {}
        objective = TaskObjective("item", "灵魄成长石", 0, 1)
        first = record_task_progress(records, "小乔", ParsedTaskProgress("高级国令慕贤", 1, 8), objective)
        second = record_task_progress(records, "小乔", ParsedTaskProgress("高级国令慕贤", 2, 8), objective)
        records.pop(task_record_key(second))
        second = replace(second, round_index=2, updated_at="2026-08-01 12:00")
        records[task_record_key(second)] = second
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        filtered = filter_task_progress_records(
            records, role="小乔", task="高级国令慕贤", round_index=1, date=today
        )
        self.assertEqual(filtered, [first])


if __name__ == "__main__":
    unittest.main()
