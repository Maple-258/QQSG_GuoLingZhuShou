import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from PIL import Image

from guoling_task_ocr.app import (
    CAPTURE_METHODS,
    CLOUD_TASK_OCR_MAX_PIXELS,
    DEFAULT_AUTO_INTERVAL_SECONDS,
    DEFAULT_HOTKEY,
    ensure_writable_error_stream,
    find_hud_role_name,
    player_info_crop_box,
    load_user_settings,
    ocr_model_directories,
    save_user_settings,
    should_skip_unchanged_task,
    cloud_task_ocr_target_size,
    cloud_task_crop_box,
    task_ocr_target_size,
)


class PreferencesTests(unittest.TestCase):
    def test_missing_error_stream_is_replaced_for_windowed_downloads(self) -> None:
        with patch("guoling_task_ocr.app.sys.stderr", None), patch("builtins.open", mock_open()) as mocked_open:
            ensure_writable_error_stream()
            mocked_open.assert_called_once()

    def test_saved_preferences_are_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "settings.json"
            save_user_settings(
                {
                    "capture_method": CAPTURE_METHODS[0],
                    "interval_seconds": 5.0,
                    "hotkey": "f7",
                    "window_title": "QQ三国测试窗口",
                    "flash_title_filter": "QQ三国",
                    "flash_target_mode": "keyword",
                    "flash_window_title": "QQ三国测试窗口",
                    "flash_sound_mode": "beep",
                    "flash_wav_path": "C:\\alert.wav",
                    "flash_cooldown_seconds": 4.5,
                    "flash_enabled": True,
                    "task_tracker_role": "小乔",
                    "window_role_bindings": {"QQ 三国测试窗口": "小乔"},
                    "show_changelog_on_start": False,
                    "market_account": "market-user",
                    "market_token": "market-token",
                    "market_user_id": "12345",
                    "market_region": "得陇",
                    "market_auto_query": True,
                    "ocr_mode": "cloud",
                    "cloud_ocr_token": "test-token",
                    "cloud_ocr_model": "PP-OCRv6",
                    "cloud_ocr_api_url": "https://api.example.test/predict",
                },
                settings_path,
            )
            self.assertEqual(
                load_user_settings(settings_path),
                {
                    "capture_method": CAPTURE_METHODS[0],
                    "interval_seconds": 5.0,
                    "hotkey": "f7",
                    "window_title": "QQ三国测试窗口",
                    "flash_title_filter": "QQ三国",
                    "flash_target_mode": "keyword",
                    "flash_window_title": "QQ三国测试窗口",
                    "flash_sound_mode": "beep",
                    "flash_wav_path": "C:\\alert.wav",
                    "flash_cooldown_seconds": 4.5,
                    "flash_enabled": True,
                    "task_tracker_role": "小乔",
                    "window_role_bindings": {"QQ 三国测试窗口": "小乔"},
                    "show_changelog_on_start": False,
                    "market_account": "market-user",
                    "market_token": "market-token",
                    "market_user_id": "12345",
                    "market_region": "得陇",
                    "market_auto_query": True,
                    "ocr_mode": "cloud",
                    "cloud_ocr_token": "test-token",
                    "cloud_ocr_model": "PP-OCRv6",
                    "cloud_ocr_api_url": "https://api.example.test/predict",
                },
            )

    def test_invalid_preferences_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "settings.json"
            settings_path.write_text(
                json.dumps({"capture_method": "unknown", "interval_seconds": 100, "hotkey": ""}),
                encoding="utf-8",
            )
            settings = load_user_settings(settings_path)
            self.assertEqual(settings["capture_method"], CAPTURE_METHODS[1])
            self.assertEqual(settings["interval_seconds"], DEFAULT_AUTO_INTERVAL_SECONDS)
            self.assertEqual(settings["hotkey"], DEFAULT_HOTKEY)

    def test_model_directories_are_under_the_supplied_cache_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory) / "ocr_models"
            directories = ocr_model_directories(cache_root)
            self.assertTrue(cache_root.is_dir())
            self.assertEqual(Path(directories["det_model_dir"]).parent, cache_root)
            self.assertEqual(Path(directories["rec_model_dir"]).parent, cache_root)
            self.assertEqual(Path(directories["cls_model_dir"]).parent, cache_root)

    def test_task_ocr_scale_keeps_small_crops_clear_and_caps_large_ones(self) -> None:
        self.assertEqual(task_ocr_target_size((400, 200)), (1600, 800))
        width, height = task_ocr_target_size((1600, 900))
        self.assertLessEqual(width * height, 4_010_000)
        self.assertLess(width, 1600 * 4)

    def test_cloud_task_ocr_scale_uses_smaller_uploads(self) -> None:
        self.assertEqual(cloud_task_ocr_target_size((400, 200)), (800, 400))
        width, height = cloud_task_ocr_target_size((1600, 900))
        self.assertLessEqual(width * height, CLOUD_TASK_OCR_MAX_PIXELS)

    def test_cloud_task_crop_covers_the_complete_task_card(self) -> None:
        image = Image.new("RGB", (1028, 800))
        self.assertEqual(cloud_task_crop_box(image), (806, 308, 1020, 440))

    def test_only_realtime_mode_skips_an_unchanged_task(self) -> None:
        signature = b"same-task"
        self.assertFalse(should_skip_unchanged_task(False, signature, signature))
        self.assertTrue(should_skip_unchanged_task(True, signature, signature))

    def test_hud_role_detection_prefers_name_beside_a_misread_level(self) -> None:
        entries = [
            ([[45, 30], [95, 30], [95, 50], [45, 50]], "Sv120"),
            ([[108, 30], [190, 30], [190, 50], [108, 50]], "Maplescx"),
            ([[50, 65], [86, 65], [86, 84], [50, 84]], "生命"),
        ]
        self.assertEqual(find_hud_role_name(entries), "Maplescx")

    def test_hud_role_detection_rejects_uppercase_level_misread_as_a_role(self) -> None:
        entries = [([ [45, 30], [95, 30], [95, 50], [45, 50] ], "L7120")]
        self.assertIsNone(find_hud_role_name(entries))

    def test_hud_role_detection_rejects_prefixed_level_misread_as_a_role(self) -> None:
        entries = [([ [45, 30], [110, 30], [110, 50], [45, 50] ], "gLv120")]
        self.assertIsNone(find_hud_role_name(entries))

    def test_player_info_crop_excludes_a_window_title_bar(self) -> None:
        image = Image.new("RGB", (1280, 760))
        self.assertEqual(player_info_crop_box(image, (8, 31, 1272, 752)), (27, 42, 387, 161))


if __name__ == "__main__":
    unittest.main()
