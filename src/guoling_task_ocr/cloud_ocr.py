"""Client for PaddleOCR's asynchronous cloud OCR job API."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import time
from typing import Any

import requests
from PIL import Image


JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PP-OCRv6"
DEFAULT_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useTextlineOrientation": False,
}
DIRECT_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


class CloudOcrError(RuntimeError):
    """A safe, user-facing cloud OCR failure."""


class CloudOcrClient:
    """Submit one image and return OCR text with image-relative boxes."""

    def __init__(
        self,
        job_url: str = JOB_URL,
        poll_interval_seconds: float = 0.4,
        timeout_seconds: float = 90.0,
        request_module: Any = requests,
    ) -> None:
        self.job_url = job_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.request_module = request_module

    def recognize(
        self, image: Image.Image, token: str, model: str = DEFAULT_MODEL, api_url: str = "",
    ) -> list[tuple[list[list[float]], str]]:
        token = token.strip()
        if not token:
            raise CloudOcrError("请先在“工具与设置 → 云端 OCR 设置”中填写 API 令牌。")
        if api_url.strip() and api_url.rstrip("/") != self.job_url:
            return self._recognize_direct(image, token, api_url.strip())
        job_id = self._submit(image, token, model)
        json_url = self._wait_for_result(job_id, token)
        try:
            response = self.request_module.get(json_url, timeout=20)
        except requests.RequestException as error:
            raise CloudOcrError("云端 OCR 结果下载失败。") from error
        if response.status_code != 200:
            raise CloudOcrError("云端 OCR 结果下载失败。")
        entries = parse_jsonl_entries(response.text)
        if not entries:
            raise CloudOcrError("云端 OCR 未返回可用文字。")
        return entries

    def _recognize_direct(
        self, image: Image.Image, token: str, api_url: str,
    ) -> list[tuple[list[list[float]], str]]:
        encoded_image = BytesIO()
        image.convert("RGB").save(encoded_image, format="JPEG", quality=92, optimize=True)
        payload = {
            "file": base64.b64encode(encoded_image.getvalue()).decode("ascii"),
            "fileType": 1,
            **DIRECT_OPTIONAL_PAYLOAD,
        }
        try:
            response = self.request_module.post(
                api_url,
                headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
        except requests.RequestException as error:
            raise CloudOcrError("云端 OCR 请求失败，请检查网络、服务地址和 API 令牌。") from error
        if response.status_code != 200:
            raise CloudOcrError("云端 OCR 请求失败，请检查服务地址和 API 令牌。")
        try:
            response_payload = response.json()
        except (ValueError, TypeError) as error:
            raise CloudOcrError("云端 OCR 返回了无法读取的结果。") from error
        if not isinstance(response_payload, dict) or not isinstance(response_payload.get("result"), dict):
            raise CloudOcrError("云端 OCR 未返回识别结果。")
        entries = parse_jsonl_entries(json.dumps(response_payload, ensure_ascii=False))
        if not entries:
            raise CloudOcrError("云端 OCR 未返回可用文字。")
        return entries

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"bearer {token}"}

    def _submit(self, image: Image.Image, token: str, model: str) -> str:
        encoded_image = BytesIO()
        image.convert("RGB").save(encoded_image, format="JPEG", quality=92, optimize=True)
        encoded_image.seek(0)
        try:
            response = self.request_module.post(
                self.job_url,
                headers=self._headers(token),
                data={"model": model, "optionalPayload": json.dumps(DEFAULT_OPTIONAL_PAYLOAD)},
                files={"file": ("qqsg-task.jpg", encoded_image, "image/jpeg")},
                timeout=30,
            )
        except requests.RequestException as error:
            raise CloudOcrError("云端 OCR 请求失败，请检查网络和 API 令牌。") from error
        payload = _response_data(response, "云端 OCR 请求失败，请检查网络和 API 令牌。")
        job_id = str(payload.get("jobId", "")).strip()
        if not job_id:
            raise CloudOcrError("云端 OCR 未返回任务编号。")
        return job_id

    def _wait_for_result(self, job_id: str, token: str) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                response = self.request_module.get(f"{self.job_url}/{job_id}", headers=self._headers(token), timeout=20)
            except requests.RequestException as error:
                raise CloudOcrError("云端 OCR 状态查询失败。") from error
            data = _response_data(response, "云端 OCR 状态查询失败。")
            state = str(data.get("state", "")).lower()
            if state == "done":
                result_url = data.get("resultUrl")
                json_url = result_url.get("jsonUrl") if isinstance(result_url, dict) else ""
                if isinstance(json_url, str) and json_url.strip():
                    return json_url.strip()
                raise CloudOcrError("云端 OCR 未返回结果文件。")
            if state == "failed":
                raise CloudOcrError("云端 OCR 任务失败。")
            time.sleep(self.poll_interval_seconds)
        raise CloudOcrError("云端 OCR 等待超时，请稍后重试。")


def parse_jsonl_entries(content: str) -> list[tuple[list[list[float]], str]]:
    """Extract text from both PP-OCR job JSONL and current PaddleOCR API results."""
    entries: list[tuple[list[list[float]], str]] = []
    seen: set[tuple[tuple[tuple[float, float], ...], str]] = set()
    for payload in _json_records(content):
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        for page in _result_pages(result):
            entries_before_page = len(entries)
            for object_value in _iter_objects(page):
                texts = _first_list(object_value, "rec_texts", "recTexts", "texts")
                boxes = _first_list(object_value, "dt_polys", "rec_polys", "rec_boxes", "recBoxes", "boxes")
                for index, text in enumerate(texts):
                    normalized_text = str(text).strip()
                    polygon = _polygon(boxes[index] if index < len(boxes) else None)
                    if normalized_text and polygon is not None:
                        _append_entry(entries, seen, polygon, normalized_text)
            if len(entries) == entries_before_page:
                _append_plain_text_entries(entries, seen, page)
    return entries


def _json_records(content: str) -> list[dict[str, Any]]:
    """Accept either JSONL downloads or a regular JSON response body."""
    try:
        decoded = json.loads(content)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]

    records: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _response_data(response: Any, message: str) -> dict[str, Any]:
    if getattr(response, "status_code", 0) != 200:
        raise CloudOcrError(message)
    try:
        payload = response.json()
    except (ValueError, TypeError) as error:
        raise CloudOcrError(message) from error
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise CloudOcrError(message)
    return data


def _result_pages(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    pages = (
        result.get("ocrResults")
        or result.get("ocr_results")
        or result.get("layoutParsingResults")
        or result.get("pages")
        or [result]
    )
    if isinstance(pages, dict):
        pages = [pages]
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def _first_list(page: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = page.get(key)
        if isinstance(value, list):
            return value
    return []


def _iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_objects(child)


def _append_plain_text_entries(
    entries: list[tuple[list[list[float]], str]],
    seen: set[tuple[tuple[tuple[float, float], ...], str]],
    page: dict[str, Any],
) -> None:
    for object_value in _iter_objects(page):
        for key in ("ocrText", "ocr_text", "text"):
            value = object_value.get(key)
            if isinstance(value, str):
                _append_text_lines(entries, seen, value)


def _append_text_lines(
    entries: list[tuple[list[list[float]], str]],
    seen: set[tuple[tuple[tuple[float, float], ...], str]],
    value: str,
) -> None:
    for line in value.splitlines():
        text = line.strip().lstrip("#>*- ").strip()
        if not text:
            continue
        index = len(entries)
        polygon = [[0.0, float(index * 24)], [1200.0, float(index * 24)], [1200.0, float(index * 24 + 20)], [0.0, float(index * 24 + 20)]]
        _append_entry(entries, seen, polygon, text)


def _append_entry(
    entries: list[tuple[list[list[float]], str]],
    seen: set[tuple[tuple[tuple[float, float], ...], str]],
    polygon: list[list[float]],
    text: str,
) -> None:
    key = (tuple((point[0], point[1]) for point in polygon), text)
    if key not in seen:
        seen.add(key)
        entries.append((polygon, text))


def _polygon(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list):
        return None
    if len(value) == 4 and all(isinstance(coordinate, (int, float)) for coordinate in value):
        left, top, right, bottom = (float(coordinate) for coordinate in value)
        return [[left, top], [right, top], [right, bottom], [left, bottom]]
    if len(value) >= 4 and all(isinstance(point, list) and len(point) >= 2 for point in value):
        return [[float(point[0]), float(point[1])] for point in value]
    return None
