"""Client for the user-authorized QQ SG market-query website."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
import time
import uuid
import urllib.error
import urllib.request
import zlib
from typing import Any


MARKET_API_BASE_URL = "http://zscx.sgbaodian.com:3588"
MARKET_WEB_URL = "http://zscx.sgbaodian.com/#/"
_SIGNING_KEY = "6JFzFFN5527IYdDf16VlBxErt96NTX18"
_REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "http://zscx.sgbaodian.com",
    "Referer": "http://zscx.sgbaodian.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0 Safari/537.36",
    "type": "webPage",
}


class MarketQueryError(RuntimeError):
    """A user-facing failure returned by the third-party market service."""


@dataclass(frozen=True)
class MarketSession:
    """Reusable website authorization. Passwords are intentionally excluded."""

    account: str
    token: str
    user_id: str


def _signed_payload(data: dict[str, Any], now: int | None = None, guid: str | None = None) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else now
    client_guid = str(uuid.uuid4()) if guid is None else guid
    signature_source = f"clientGuid={client_guid}&clientTimestamp={timestamp}&key={_SIGNING_KEY}"
    payload = dict(data)
    payload.update({
        "clientTimestamp": timestamp,
        "clientGuid": client_guid,
        "sign": hashlib.md5(signature_source.encode("utf-8")).hexdigest(),
    })
    return payload


def decode_market_data(encoded: str) -> dict[str, Any]:
    """Decode the compressed listing body returned by the market website."""
    try:
        byte_values = base64.b64decode(encoded).decode("ascii").split(",")
        compressed = bytes(int(value) for value in byte_values if value)
    except (UnicodeDecodeError, ValueError, base64.binascii.Error) as error:
        raise MarketQueryError("摊位数据格式无法识别。") from error

    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 16):
        try:
            return json.loads(zlib.decompress(compressed, wbits).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, zlib.error):
            continue
    raise MarketQueryError("摊位数据解压失败。")


def _decode_response_data(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        return decode_market_data(data)
    raise MarketQueryError("摊位查询未返回可用数据。")


class MarketClient:
    """Small, dependency-free API client for website users' own accounts."""

    def __init__(self, api_base_url: str = MARKET_API_BASE_URL) -> None:
        self.api_base_url = api_base_url.rstrip("/")

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.api_base_url}{path}",
            data=json.dumps(_signed_payload(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=_REQUEST_HEADERS,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise MarketQueryError(f"行情网站连接失败：{error}") from error
        if not isinstance(payload, dict):
            raise MarketQueryError("行情网站返回了无效响应。")
        if str(payload.get("code", "")) != "200":
            raise MarketQueryError(str(payload.get("msg") or "行情网站拒绝了本次请求。"))
        return payload

    def login(self, account: str, password: str) -> MarketSession:
        payload = self._post("/qqsg/login", {"account": account, "password": password})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MarketQueryError("登录成功，但未收到授权信息。")
        token = str(data.get("token", "")).strip()
        user_id = str(data.get("userId", "")).strip()
        resolved_account = str(data.get("account") or account).strip()
        if not token or not user_id:
            raise MarketQueryError("登录成功，但授权信息不完整。")
        return MarketSession(account=resolved_account, token=token, user_id=user_id)

    def list_regions(self) -> list[str]:
        payload = self._post("/qqsg/allRegion", {})
        data = payload.get("data")
        if not isinstance(data, list):
            raise MarketQueryError("行情网站未返回区服列表。")
        return [str(record.get("region", "")).strip() for record in data if isinstance(record, dict) and record.get("region")]

    def query_listings(self, session: MarketSession, region: str, keyword: str) -> dict[str, Any]:
        payload = self._post(
            "/qqsg/getList",
            {"region": region, "keyword": keyword, "token": session.token, "userId": session.user_id},
        )
        return _decode_response_data(payload.get("data"))


def flatten_listings(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize the site's abbreviated listing fields for the desktop table."""
    groups = (
        ("摊位", payload.get("boothInitData")),
        ("商行", payload.get("shopInitData")),
        ("寄卖", payload.get("consignmentInitData")),
    )
    rows: list[dict[str, str]] = []
    for source, records in groups:
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            coordinate = _format_coordinate(record)
            owner = next((str(record[key]) for key in ("tzm", "dzm", "userName", "name") if record.get(key)), "-")
            price = next((str(record[key]) for key in ("jg", "price", "unitPrice") if record.get(key)), "-")
            name = next((str(record[key]) for key in ("wpm", "goodsName", "itemName", "name") if record.get(key)), "-")
            quantity = next((str(record[key]) for key in ("sl", "quantity", "count", "num") if record.get(key) is not None), "-")
            stall_info = next((str(record[key]) for key in ("shmc", "twm", "boothName", "shopName", "boothId", "shopId") if record.get(key)), "-")
            detail = "  ".join(f"{key}: {value}" for key, value in record.items() if value not in (None, ""))
            rows.append({
                "source": source,
                "name": name,
                "quantity": quantity,
                "price": price,
                "owner": owner,
                "stall_info": stall_info,
                "coordinate": coordinate,
                "detail": detail,
            })
    return sorted(rows, key=_price_sort_key)


def _format_coordinate(record: dict[str, Any]) -> str:
    """Turn the website's pinyin abbreviations into the in-game location format."""
    merchant_id = str(record.get("shid") or "").strip()
    shop_id = str(record.get("dpid") or "").strip()
    if merchant_id or shop_id:
        merchant_location = f"{merchant_id}号商行" if merchant_id else "商行"
        return f"{merchant_location} {shop_id}号店铺" if shop_id else merchant_location

    direct_coordinate = next(
        (str(record[key]) for key in ("坐标", "coordinate", "coordinates", "location", "address") if record.get(key)),
        "",
    )
    if direct_coordinate:
        return direct_coordinate

    map_name = str(record.get("ditu") or record.get("map") or "").strip()
    map_name = {"子城": "成都·子城"}.get(map_name, map_name)
    line = str(record.get("xian") or "").strip()
    position = str(record.get("zuobiao") or "").strip()
    parts = []
    if line:
        parts.append(f"{line}线")
    if map_name:
        parts.append(map_name)
    if position:
        parts.append(position)
    return " ".join(parts) if parts else "-"


def _price_sort_key(row: dict[str, str]) -> float:
    """Sort prices numerically while keeping entries without a price at the end."""
    try:
        return float(row["price"].replace(",", "").strip())
    except (KeyError, ValueError):
        return math.inf
