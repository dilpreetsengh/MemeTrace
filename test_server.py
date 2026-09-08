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


if __name__ == "__main__":
    unittest.main()
