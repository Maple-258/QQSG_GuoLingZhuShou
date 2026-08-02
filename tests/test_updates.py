import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guoling_task_ocr.app import (
    fetch_latest_release,
    is_newer_version,
    load_changelog,
    parse_version,
    render_changelog,
)


class UpdateInformationTests(unittest.TestCase):
    def test_parses_standard_release_versions(self) -> None:
        self.assertEqual(parse_version("v1.3.0"), (1, 3, 0))
        self.assertEqual(parse_version("1.3"), (1, 3, 0))
        self.assertIsNone(parse_version("latest"))

    def test_compares_release_versions(self) -> None:
        self.assertTrue(is_newer_version("v1.3.1", "1.3.0"))
        self.assertFalse(is_newer_version("v1.3.0", "1.3.0"))
        self.assertFalse(is_newer_version("invalid", "1.3.0"))

    def test_loads_changelog_from_supplied_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            changelog_path = Path(temporary_directory) / "CHANGELOG.md"
            changelog_path.write_text("# 更新日志", encoding="utf-8")
            self.assertEqual(load_changelog(changelog_path), "# 更新日志")

    def test_renders_markdown_changelog_without_source_markers(self) -> None:
        rendered = render_changelog("# 更新日志\n\n## [1.3.8]\n\n- **新增** `步数追踪`")

        self.assertIn("更新日志", rendered)
        self.assertIn("1.3.8", rendered)
        self.assertIn("• 新增 步数追踪", rendered)
        self.assertNotIn("#", rendered)

    def test_reads_latest_release_metadata(self) -> None:
        payload = json.dumps({"tag_name": "v1.3.0", "html_url": "https://example.test/release"}).encode("utf-8")
        with patch("guoling_task_ocr.app.urllib.request.urlopen") as mocked_urlopen:
            mocked_urlopen.return_value.__enter__.return_value.read.return_value = payload
            self.assertEqual(
                fetch_latest_release("https://example.test/latest"),
                {"tag_name": "v1.3.0", "html_url": "https://example.test/release"},
            )


if __name__ == "__main__":
    unittest.main()
