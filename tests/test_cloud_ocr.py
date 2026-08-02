import json
import unittest

from PIL import Image

from guoling_task_ocr.cloud_ocr import CloudOcrClient, CloudOcrError, parse_jsonl_entries


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self.payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self) -> None:
        self.post_calls = []
        self.get_calls = []
        self.job_requests = 0

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse({"data": {"jobId": "job-42"}})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url.endswith("/job-42"):
            self.job_requests += 1
            if self.job_requests == 1:
                return FakeResponse({"data": {"state": "running"}})
            return FakeResponse({"data": {"state": "done", "resultUrl": {"jsonUrl": "https://result.test/ocr.jsonl"}}})
        return FakeResponse(text=json.dumps({"result": {"ocrResults": [{
            "rec_texts": ["任务追踪", "灵魄成长石"],
            "dt_polys": [
                [[1, 2], [20, 2], [20, 12], [1, 12]],
                [[3, 16], [50, 16], [50, 28], [3, 28]],
            ],
        }]}}))


class CloudOcrTests(unittest.TestCase):
    def test_jsonl_parser_returns_text_and_polygons(self) -> None:
        content = json.dumps({"result": {"ocrResults": [{
            "rec_texts": ["国令慕贤", "气旋秘籍(高)"],
            "dt_polys": [
                [[0, 0], [30, 0], [30, 10], [0, 10]],
                [[0, 12], [48, 12], [48, 24], [0, 24]],
            ],
        }]}})
        self.assertEqual(
            parse_jsonl_entries(content),
            [
                ([[0.0, 0.0], [30.0, 0.0], [30.0, 10.0], [0.0, 10.0]], "国令慕贤"),
                ([[0.0, 12.0], [48.0, 12.0], [48.0, 24.0], [0.0, 24.0]], "气旋秘籍(高)"),
            ],
        )

    def test_jsonl_parser_reads_job_ocr_text_without_detection_boxes(self) -> None:
        content = json.dumps({"result": {"ocrResults": [{
            "ocrText": "任务追踪\n需要 灵魄成长石 0/1",
            "ocrImage": "https://example.test/visualized.jpg",
        }]}})
        entries = parse_jsonl_entries(content)
        self.assertEqual([text for _box, text in entries], ["任务追踪", "需要 灵魄成长石 0/1"])
        self.assertEqual(entries[1][0][0], [0.0, 24.0])

    def test_jsonl_parser_reads_current_api_markdown_text(self) -> None:
        content = json.dumps({"result": {"layoutParsingResults": [{
            "markdown": {"text": "# 任务追踪\n\n需要 四阶元神升阶石 0/1"},
        }]}})
        self.assertEqual(
            [text for _box, text in parse_jsonl_entries(content)],
            ["任务追踪", "需要 四阶元神升阶石 0/1"],
        )

    def test_client_submits_polls_and_downloads_result(self) -> None:
        requests = FakeRequests()
        client = CloudOcrClient(poll_interval_seconds=0, request_module=requests)
        entries = client.recognize(Image.new("RGB", (32, 20), "white"), "private-token")
        self.assertEqual([text for _box, text in entries], ["任务追踪", "灵魄成长石"])
        self.assertEqual(requests.post_calls[0][1]["data"]["model"], "PP-OCRv6")
        self.assertEqual(requests.get_calls[-1][0], "https://result.test/ocr.jsonl")

    def test_client_uses_current_direct_api_and_markdown_result(self) -> None:
        class DirectRequests(FakeRequests):
            def post(self, url, **kwargs):
                self.post_calls.append((url, kwargs))
                return FakeResponse({"result": {"layoutParsingResults": [{
                    "markdown": {"text": "任务追踪\n需要 灵魄成长石 0/1"},
                }]}})

        requests = DirectRequests()
        entries = CloudOcrClient(request_module=requests).recognize(
            Image.new("RGB", (32, 20), "white"),
            "private-token",
            api_url="https://api.example.test/predict",
        )
        self.assertEqual([text for _box, text in entries], ["任务追踪", "需要 灵魄成长石 0/1"])
        self.assertEqual(requests.post_calls[0][1]["headers"]["Content-Type"], "application/json")
        self.assertIn("file", requests.post_calls[0][1]["json"])

    def test_empty_token_does_not_echo_a_token(self) -> None:
        with self.assertRaises(CloudOcrError) as caught:
            CloudOcrClient(request_module=FakeRequests()).recognize(Image.new("RGB", (1, 1)), "")
        self.assertNotIn("token", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
