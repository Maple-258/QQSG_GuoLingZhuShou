import unittest
from queue import Queue
from unittest.mock import patch

from guoling_task_ocr.flash_alert import FlashEvent, FlashMonitor, matches_event


class FlashAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = FlashEvent(hwnd=1234, title="QQ三国 - 国令慕贤", source="ShellHook")

    def test_window_mode_requires_a_selected_window(self) -> None:
        self.assertFalse(matches_event(self.event, "", None, "window"))

    def test_matches_title_filter_case_insensitively(self) -> None:
        self.assertTrue(matches_event(self.event, "qq三国", None, "keyword"))
        self.assertFalse(matches_event(self.event, "记事本", None, "keyword"))

    def test_matches_any_custom_keyword(self) -> None:
        self.assertTrue(matches_event(self.event, "记事本 | 国令", None, "keyword"))

    def test_matches_selected_window(self) -> None:
        self.assertTrue(matches_event(self.event, "", 1234, "window"))
        self.assertFalse(matches_event(self.event, "", 5678, "window"))

    def test_drops_event_when_queue_is_full(self) -> None:
        events: Queue[FlashEvent] = Queue(maxsize=1)
        monitor = FlashMonitor(events)
        events.put(FlashEvent(hwnd=1, title="已有事件", source="test"))

        with patch("guoling_task_ocr.flash_alert.window_title", return_value="新事件"):
            monitor._emit(2, "test")

        self.assertEqual(events.qsize(), 1)
        self.assertEqual(events.get_nowait().hwnd, 1)


if __name__ == "__main__":
    unittest.main()
