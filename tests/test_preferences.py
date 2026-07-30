import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from guoling_task_ocr.app import (
    CAPTURE_METHODS,
    DEFAULT_AUTO_INTERVAL_SECONDS,
    DEFAULT_HOTKEY,
    ensure_writable_error_stream,
    load_user_settings,
    ocr_model_directories,
    save_user_settings,
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


if __name__ == "__main__":
    unittest.main()
