import base64
import hashlib
import json
import unittest
import zlib

from guoling_task_ocr.market_query import _REQUEST_HEADERS, _signed_payload, decode_market_data, flatten_listings


class MarketQueryTests(unittest.TestCase):
    def test_request_headers_match_the_website_context(self) -> None:
        self.assertEqual(_REQUEST_HEADERS["Origin"], "http://zscx.sgbaodian.com")
        self.assertEqual(_REQUEST_HEADERS["Referer"], "http://zscx.sgbaodian.com/")

    def test_signed_payload_matches_website_format(self) -> None:
        payload = _signed_payload({"region": "得陇"}, now=1_785_503_819, guid="01d1b3ba-271e-46a7-9a79-d5c18da853e8")

        self.assertEqual(payload["region"], "得陇")
        self.assertEqual(payload["sign"], "79d8147350025eac89fc0d4ec5ceb864")

    def test_decode_compressed_listing_data(self) -> None:
        original = {"boothInitData": [{"wpm": "测试物品", "jg": "123"}]}
        compressed = zlib.compress(json.dumps(original, ensure_ascii=False).encode("utf-8"))
        encoded = base64.b64encode(",".join(str(value) for value in compressed).encode("ascii")).decode("ascii")

        self.assertEqual(decode_market_data(encoded), original)

    def test_flatten_listings_formats_abbreviated_market_fields(self) -> None:
        rows = flatten_listings({
            "boothInitData": [{
                "wpm": "玄铁石", "sl": 375, "jg": "3999", "tzm": "摊主", "twm": "作死模式开启",
                "ditu": "子城", "xian": 6, "zuobiao": "(18,15)",
            }],
            "shopInitData": [{
                "wpm": "玄铁石", "sl": 165, "jg": "3526", "tzm": "琉璃·嗫灵",
                "shid": 294, "dpid": 7, "shmc": "传奇",
            }],
        })

        self.assertEqual(rows[0]["source"], "商行")
        self.assertEqual(rows[0]["quantity"], "165")
        self.assertEqual(rows[0]["stall_info"], "传奇")
        self.assertEqual(rows[0]["coordinate"], "294号商行 7号店铺")
        self.assertEqual(rows[1]["source"], "摊位")
        self.assertEqual(rows[1]["coordinate"], "6线 成都·子城 (18,15)")
        self.assertIn("玄铁石", rows[0]["detail"])


if __name__ == "__main__":
    unittest.main()
