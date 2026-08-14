import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "collect_infostock.py"
SPEC = importlib.util.spec_from_file_location("collect_infostock", MODULE_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class CollectInfostockTests(unittest.TestCase):
    def test_parse_stock_pairs_preserves_codes_and_order(self):
        result = collector.parse_stock_pairs("027360-아주IB투자|100790-미래에셋벤처투자")

        self.assertEqual([item["sourceOrder"] for item in result], [0, 1])
        self.assertEqual([item["stockCode"] for item in result], ["027360", "100790"])
        self.assertEqual(result[0]["name"], "아주IB투자")

    def test_normalize_theme_detail_preserves_source_metadata(self):
        response = {
            "theme": {"code": 584, "name": "스페이스X(SpaceX)", "outline": "설명"},
            "items": [
                {
                    "B2Bseq": "event-1",
                    "showDate": "20260813",
                    "createTime": "20260813104427",
                    "lastUpdateTime": "20260813161450",
                    "content": "상승 사유",
                    "LEAD_STOCK": "027360-아주IB투자",
                    "STOCKS": "027360-아주IB투자|100790-미래에셋벤처투자",
                    "CREATE_WRITER": "작성자",
                    "CHART": "0",
                }
            ],
            "stockItems": [
                {
                    "code": "347700",
                    "name": "스피어",
                    "outline": "편입 이유",
                    "index": "",
                }
            ],
        }

        result = collector.normalize_theme_detail(
            "584", response, "2026-08-14T00:00:00+00:00"
        )

        self.assertEqual(result["themeId"], "584")
        self.assertEqual(result["history"][0]["date"], "2026-08-13")
        self.assertEqual(result["history"][0]["leaders"][0]["stockCode"], "027360")
        self.assertEqual(result["relatedStocks"][0]["stockCode"], "347700")
        self.assertTrue(result["historyComplete"])
        self.assertEqual(len(result["contentHash"]), 64)

    def test_quality_summary_reports_source_anomalies_without_rejecting_import(self):
        payload = {
            "themeId": "1",
            "themeName": "테마",
            "historyComplete": True,
            "history": [
                {"date": "2026-08-13", "content": "같은 사건", "leaders": []},
                {"date": "2026-08-13", "content": "같은 사건", "leaders": []},
                {"date": None, "content": "", "leaders": [{"stockCode": None}]},
            ],
            "relatedStocks": [{"stockCode": None}],
        }

        self.assertEqual(collector.validate_theme_payload(payload), [])
        quality = collector.quality_summary(payload)
        self.assertEqual(quality["duplicateHistoryCount"], 1)
        self.assertEqual(quality["missingHistoryDateCount"], 1)
        self.assertEqual(quality["missingHistoryContentCount"], 1)
        self.assertEqual(quality["missingLeaderCodeCount"], 1)
        self.assertEqual(quality["missingRelatedStockCodeCount"], 1)

    def test_choose_themes_rejects_unknown_id(self):
        index = [{"themeId": "584", "themeName": "스페이스X(SpaceX)"}]

        with self.assertRaises(collector.CollectionError):
            collector.choose_themes(index, ["999999"])


if __name__ == "__main__":
    unittest.main()
