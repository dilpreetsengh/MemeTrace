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

    def test_full_scan_runs_each_research_gate_and_returns_a_final_summary(self):
        calls = []

        def stage(name, result):
            def run():
                calls.append(name)
                return result
            return run

        feed = {
            "summary": {"candidate": 1, "watch": 0, "avoid": 0},
            "candidates": [{"mint": "FullScanMint", "name": "Full Scan", "symbol": "FULL", "score": 84, "status": "candidate"}],
        }
        result = server.run_full_research_scan(
            discovery=stage("discovery", {"records_saved": 1}),
            quote_check=stage("quotes", {"sellable": 1, "note": "route found"}),
            safety_check=stage("safety", {"clean": 1, "note": "risk check passed"}),
            wallet_check=stage("wallet", {"clear": 1, "note": "public evidence checked"}),
            market_crosscheck=stage("crosscheck", {"consistent": 1, "note": "pool matches"}),
            feed_provider=lambda: feed,
        )

        self.assertEqual(calls, ["discovery", "quotes", "safety", "wallet", "crosscheck"])
        self.assertEqual(result["summary"]["headline"], "Research candidate found: Full Scan ($FULL)")
        self.assertEqual(result["summary"]["top_candidate"]["mint"], "FullScanMint")

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

    def test_fresh_dex_refresh_invalidates_old_quote_and_safety_evidence(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"mint": "FreshnessMint111111111111111111111111111111111", "source": "dexscreener", "price_usd": 0.02, "observed_at": 1_800_000_000})
        conn = server.database()
        server.upsert_candidate(candidate, conn)
        conn.execute("""UPDATE candidates SET sell_quote_status='pass', sell_impact_pct=0.4,
          safety_status='pass', crosscheck_status='pass', risk_flags_json='[\"old flag\"]' WHERE mint=?""", (candidate["mint"],))
        candidate["price_usd"] = 0.03
        candidate["observed_at"] += 60
        server.upsert_candidate(candidate, conn)
        conn.commit()
        refreshed = conn.execute("SELECT * FROM candidates WHERE mint=?", (candidate["mint"],)).fetchone()

        self.assertEqual(refreshed["sell_quote_status"], "pending")
        self.assertIsNone(refreshed["sell_impact_pct"])
        self.assertEqual(refreshed["safety_status"], "pending")
        self.assertEqual(refreshed["crosscheck_status"], "pending")

    def test_jupiter_sell_quote_is_saved_without_creating_a_transaction(self):
        now = 1_800_000_000
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({
            "mint": "JupiterTestMint111111111111111111111111111111", "source": "dexscreener",
            "safety_status": "pending", "price_usd": 0.02, "observed_at": now,
        })
        conn = server.database()
        server.upsert_candidate(candidate, conn)
        conn.commit()
        calls = []

        def fake_fetcher(url):
            calls.append(url)
            if "/tokens/v2/search" in url:
                return [{"address": candidate["mint"], "decimals": 6}]
            self.assertIn("/swap/v1/quote", url)
            self.assertIn("inputMint=" + candidate["mint"], url)
            return {"priceImpactPct": "1.25", "routePlan": [{"percent": 100}]}

        result = server.enrich_jupiter_sellability(fetcher=fake_fetcher, now=now)
        feed = server.candidate_feed()
        live = feed["candidates"][0]

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["sellable"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(live["sell_quote_status"], "pass")
        self.assertEqual(live["sell_impact_pct"], 1.25)
        self.assertEqual(live["status"], "watch")  # safety check still blocks a real Candidate label
        self.assertTrue(any(gate["label"] == "Small sell quote" and gate["status"] == "PASS" for gate in live["gates"]))

    def test_jupiter_empty_route_marks_the_card_avoid(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"source": "dexscreener", "price_usd": 0.01})

        def no_route_fetcher(url):
            if "/tokens/v2/search" in url:
                return [{"address": candidate["mint"], "decimals": 6}]
            return {"priceImpactPct": "0", "routePlan": []}

        quote = server.check_jupiter_sell_quote(candidate, fetcher=no_route_fetcher)
        candidate.update({"sell_quote_status": quote["status"], "sell_quote_note": quote["note"]})
        assessment = server.candidate_assessment(candidate)

        self.assertEqual(quote["status"], "no_route")
        self.assertEqual(assessment["status"], "avoid")

    def test_solana_tracker_danger_flag_is_saved_as_avoid(self):
        now = 1_800_000_000
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({
            "mint": "TrackerTestMint111111111111111111111111111111", "source": "dexscreener",
            "safety_status": "pending", "price_usd": 0.02, "observed_at": now,
        })
        conn = server.database()
        server.upsert_candidate(candidate, conn)
        conn.execute("UPDATE candidates SET sell_quote_status='pass', sell_impact_pct=0.8 WHERE mint=?", (candidate["mint"],))
        conn.commit()

        def fake_fetcher(mint):
            self.assertEqual(mint, candidate["mint"])
            return {"risk": {"score": 7.4, "rugged": False, "risks": [
                {"name": "Freeze Authority Enabled", "description": "Creator can freeze tokens.", "level": "danger"},
            ]}}

        result = server.enrich_solana_tracker_risk(fetcher=fake_fetcher, now=now)
        live = server.candidate_feed()["candidates"][0]

        self.assertEqual(result["flagged"], 1)
        self.assertEqual(live["safety_status"], "avoid")
        self.assertEqual(live["safety_score"], 7.4)
        self.assertEqual(live["status"], "avoid")
        self.assertIn("Freeze Authority Enabled", live["risk_flags"][0])

    def test_solana_tracker_clean_result_waits_for_wallet_evidence(self):
        result = server.interpret_solana_tracker_risk({"risk": {"score": 2, "rugged": False, "risks": []}})
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"source": "dexscreener", "safety_status": result["status"], "safety_note": result["note"]})

        assessment = server.candidate_assessment(candidate)

        self.assertEqual(result["status"], "tracker_pass")
        self.assertEqual(assessment["status"], "watch")
        self.assertTrue(any(gate["label"] == "Safety / cluster flags" and gate["status"] == "WATCH" for gate in assessment["gates"]))

    def test_helius_public_wallet_evidence_completes_the_safety_gate(self):
        now = 1_800_000_000
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({
            "mint": "HeliusTestMint1111111111111111111111111111111", "source": "dexscreener",
            "safety_status": "tracker_pass", "price_usd": 0.02, "observed_at": now,
        })
        conn = server.database()
        server.upsert_candidate(candidate, conn)
        conn.execute("UPDATE candidates SET sell_quote_status='pass', sell_impact_pct=0.8, safety_status='tracker_pass' WHERE mint=?", (candidate["mint"],))
        conn.commit()
        creator = "Creator111111111111111111111111111111111111"

        def fake_asset_fetcher(mint):
            self.assertEqual(mint, candidate["mint"])
            return {"creators": [{"address": creator, "verified": True}], "authorities": [],
                    "token_info": {"mint_authority": None, "freeze_authority": None}}

        def fake_transactions(address):
            self.assertEqual(address, creator)
            return [{"tokenTransfers": [{"mint": candidate["mint"], "fromUserAccount": creator, "toUserAccount": "Elsewhere"}]}]

        result = server.enrich_helius_wallet_evidence(fake_asset_fetcher, fake_transactions, now)
        live = server.candidate_feed()["candidates"][0]

        self.assertEqual(result["clear"], 1)
        self.assertEqual(live["safety_status"], "pass")
        self.assertEqual(live["creator_address"], creator)
        self.assertEqual(live["wallet_evidence"]["recent_token_outflows_from_observed_address"], 1)
        self.assertEqual(live["status"], "watch")  # CoinGecko cross-check is still required.

    def test_helius_active_mint_authority_is_a_hard_flag(self):
        result = server.interpret_helius_wallet_evidence(
            {"creators": [], "authorities": [], "token_info": {"mint_authority": "ActiveMint", "freeze_authority": None}},
            [],
            "test-mint",
        )

        self.assertEqual(result["status"], "avoid")
        self.assertIn("mint authority", result["flags"][0])

    def test_coingecko_consistent_pool_data_completes_final_candidate_gate(self):
        now = 1_800_000_000
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({
            "mint": "GeckoTestMint11111111111111111111111111111111", "source": "dexscreener",
            "safety_status": "pass", "price_usd": 0.02, "liquidity_usd": 60_000,
            "pair_address": "GeckoTestPair111", "observed_at": now,
        })
        conn = server.database()
        server.upsert_candidate(candidate, conn)
        conn.execute("UPDATE candidates SET sell_quote_status='pass', sell_impact_pct=0.8, safety_status='pass' WHERE mint=?", (candidate["mint"],))
        conn.commit()

        def fake_fetcher(pair_address):
            self.assertEqual(pair_address, candidate["pair_address"])
            return {"attributes": {"base_token_price_usd": "0.019", "reserve_in_usd": "57_000"}}

        result = server.enrich_coingecko_crosscheck(fetcher=fake_fetcher, now=now)
        live = server.candidate_feed()["candidates"][0]

        self.assertEqual(result["consistent"], 1)
        self.assertEqual(live["crosscheck_status"], "pass")
        self.assertEqual(live["crosscheck_price_usd"], 0.019)
        self.assertEqual(live["status"], "candidate")

    def test_coingecko_large_price_gap_keeps_card_watch(self):
        candidate = dict(server.SAMPLE_CANDIDATES[0])
        candidate.update({"source": "dexscreener", "price_usd": 0.02, "liquidity_usd": 60_000})
        result = server.interpret_coingecko_pool(candidate, {"attributes": {"base_token_price_usd": "0.01", "reserve_in_usd": "60_000"}})
        candidate.update({"safety_status": "pass", "sell_quote_status": "pass", "sell_impact_pct": 0.8,
                          "crosscheck_status": result["status"], "crosscheck_note": result["note"]})

        assessment = server.candidate_assessment(candidate)

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(assessment["status"], "watch")


if __name__ == "__main__":
    unittest.main()
