import tempfile
import unittest
from pathlib import Path

import server


class CandidateFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = server.DB_PATH
        server.DB_PATH = Path(self.temp_dir.name) / "test-memetrace.db"

    def tearDown(self):
        server.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_sample_feed_has_candidate_watch_and_avoid_states(self):
        feed = server.candidate_feed()
        statuses = {candidate["symbol"]: candidate["status"] for candidate in feed["candidates"]}

        self.assertTrue(feed["is_sample_data"])
        self.assertEqual(feed["summary"]["candidate"], 2)
        self.assertEqual(feed["summary"]["watch"], 1)
        self.assertEqual(feed["summary"]["avoid"], 2)
        self.assertEqual(statuses["CINDER"], "candidate")
        self.assertEqual(statuses["PIGEON"], "watch")
        self.assertEqual(statuses["GLIM"], "avoid")

    def test_thin_liquidity_and_bad_sellability_force_avoid(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"liquidity_usd": 9_000, "sell_impact_pct": 7.5})

        assessment = server.candidate_assessment(candidate)

        self.assertEqual(assessment["status"], "avoid")
        self.assertIn("liquidity is below $25k", assessment["reasons"][0])
        self.assertIn("estimated sell impact is above 3%", assessment["reasons"][0])

    def test_live_record_with_pending_safety_cannot_be_candidate(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"source": "dexscreener", "safety_status": "pending"})

        assessment = server.candidate_assessment(candidate)

        self.assertEqual(assessment["status"], "watch")
        self.assertTrue(any(gate["status"] == "PENDING" for gate in assessment["gates"]))

    def test_dexscreener_refresh_saves_a_qualified_live_watch_card(self):
        now = 1_800_000_000
        mint = "LiveDexMint111111111111111111111111111111111"

        def fake_fetcher(url):
            if url.endswith("/token-profiles/latest/v1"):
                return [
                    {"chainId": "base", "tokenAddress": "ignore-base"},
                    {"chainId": "solana", "tokenAddress": mint},
                ]
            self.assertIn(mint, url)
            return [{
                "chainId": "solana", "pairAddress": "LiveDexPair111",
                "baseToken": {"address": mint, "name": "Live Test", "symbol": "LIVE"},
                "marketCap": 220_000, "liquidity": {"usd": 61_000},
                "volume": {"m5": 12_000}, "txns": {"m5": {"buys": 44, "sells": 20}},
                "priceChange": {"m5": 8.2}, "pairCreatedAt": (now - 900) * 1000,
            }]

        result = server.refresh_dexscreener_candidates(fetcher=fake_fetcher, now=now)
        feed = server.candidate_feed()

        self.assertEqual(result["records_saved"], 1)
        self.assertFalse(feed["is_sample_data"])
        self.assertEqual(feed["summary"]["live"], 1)
        self.assertEqual(len(feed["candidates"]), 1)
        self.assertEqual(feed["candidates"][0]["symbol"], "LIVE")
        self.assertEqual(feed["candidates"][0]["status"], "watch")

    def test_dexscreener_filter_rejects_thin_pair(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"liquidity_usd": 24_999, "age_minutes": 10, "volume_5m_usd": 2_000})
        self.assertFalse(server.passes_dex_discovery_filter(candidate))


if __name__ == "__main__":
    unittest.main()
