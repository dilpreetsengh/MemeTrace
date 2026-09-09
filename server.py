"""MemeTrace local research server.

Public-chain research only. This server never connects a wallet, trades, or sends a transaction.
Run: python3 server.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent


def load_local_env() -> None:
    """Load simple KEY=value pairs from a local .env file without another package."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()
DB_PATH = ROOT / "memetrace.db"
HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")

STATUS_ORDER = {"candidate": 0, "watch": 1, "avoid": 2}
VALID_STATUSES = set(STATUS_ORDER)
DEX_SCREENER_API_BASE = "https://api.dexscreener.com"
DEX_DISCOVERY_MAX_TOKENS = 15
DEX_DISCOVERY_MIN_MARKET_CAP_USD = 75_000
DEX_DISCOVERY_MAX_MARKET_CAP_USD = 3_000_000
DEX_DISCOVERY_MIN_LIQUIDITY_USD = 25_000
DEX_DISCOVERY_MIN_AGE_MINUTES = 5
DEX_DISCOVERY_MIN_5M_VOLUME_USD = 1_000
JUPITER_API_BASE = "https://api.jup.ag"
JUPITER_TEST_SELL_USD = 5
JUPITER_MAX_QUOTES_PER_CHECK = 3
SOL_MINT = "So11111111111111111111111111111111111111112"

# Fictional fixtures let us review the first candidate feed before any live
# market, quote, safety, or wallet provider is connected.
SAMPLE_CANDIDATES: list[dict[str, Any]] = [
    {
        "mint": "sample-cinder-squirrel", "name": "Cinder Squirrel", "symbol": "CINDER",
        "chain": "solana", "pair_address": "sample-pair-cinder", "source": "sample",
        "setup": "early_momentum", "market_cap_usd": 218_000, "liquidity_usd": 62_000,
        "volume_5m_usd": 35_400, "buys_5m": 124, "sells_5m": 58, "age_minutes": 22,
        "price_change_5m_pct": 18.4, "previous_high_market_cap_usd": None,
        "sell_impact_pct": 1.1, "risk_flags": [], "safety_status": "sample",
    },
    {
        "mint": "sample-tinfoil-toad", "name": "Tinfoil Toad", "symbol": "TOAD",
        "chain": "solana", "pair_address": "sample-pair-toad", "source": "sample",
        "setup": "panic_reclaim", "market_cap_usd": 294_000, "liquidity_usd": 71_000,
        "volume_5m_usd": 24_800, "buys_5m": 88, "sells_5m": 47, "age_minutes": 74,
        "price_change_5m_pct": 7.2, "previous_high_market_cap_usd": 720_000,
        "sell_impact_pct": 1.7, "risk_flags": [], "safety_status": "sample",
    },
    {
        "mint": "sample-pigeon-protocol", "name": "Pigeon Protocol", "symbol": "PIGEON",
        "chain": "solana", "pair_address": "sample-pair-pigeon", "source": "sample",
        "setup": "early_momentum", "market_cap_usd": 132_000, "liquidity_usd": 31_000,
        "volume_5m_usd": 7_100, "buys_5m": 39, "sells_5m": 34, "age_minutes": 9,
        "price_change_5m_pct": 2.1, "previous_high_market_cap_usd": None,
        "sell_impact_pct": 2.6, "risk_flags": [], "safety_status": "sample",
    },
    {
        "mint": "sample-glimmer-pup", "name": "Glimmer Pup", "symbol": "GLIM",
        "chain": "solana", "pair_address": "sample-pair-glim", "source": "sample",
        "setup": "early_momentum", "market_cap_usd": 176_000, "liquidity_usd": 14_500,
        "volume_5m_usd": 10_800, "buys_5m": 96, "sells_5m": 43, "age_minutes": 13,
        "price_change_5m_pct": 23.0, "previous_high_market_cap_usd": None,
        "sell_impact_pct": 6.8, "risk_flags": [], "safety_status": "sample",
    },
    {
        "mint": "sample-astro-cabbage", "name": "Astro Cabbage", "symbol": "CABBAGE",
        "chain": "solana", "pair_address": "sample-pair-cabbage", "source": "sample",
        "setup": "panic_reclaim", "market_cap_usd": 356_000, "liquidity_usd": 84_000,
        "volume_5m_usd": 41_600, "buys_5m": 111, "sells_5m": 52, "age_minutes": 51,
        "price_change_5m_pct": 10.7, "previous_high_market_cap_usd": 810_000,
        "sell_impact_pct": 1.4,
        "risk_flags": ["sample creator-linked cluster concentration"], "safety_status": "sample",
    },
]


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS wallets (
      address TEXT PRIMARY KEY, first_seen INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tokens (
      mint TEXT PRIMARY KEY, first_seen INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trades (
      signature TEXT NOT NULL, wallet TEXT NOT NULL, mint TEXT NOT NULL,
      timestamp INTEGER NOT NULL, side TEXT NOT NULL, amount REAL,
      source TEXT NOT NULL, PRIMARY KEY(signature, wallet, mint, side)
    );
    CREATE INDEX IF NOT EXISTS trades_wallet_idx ON trades(wallet);
    CREATE INDEX IF NOT EXISTS trades_mint_idx ON trades(mint, timestamp);
    CREATE TABLE IF NOT EXISTS imports (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, created_at INTEGER NOT NULL,
      records INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS candidates (
      mint TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      symbol TEXT NOT NULL,
      chain TEXT NOT NULL,
      pair_address TEXT,
      source TEXT NOT NULL,
      setup TEXT NOT NULL,
      price_usd REAL NOT NULL DEFAULT 0,
      market_cap_usd REAL NOT NULL,
      liquidity_usd REAL NOT NULL,
      volume_5m_usd REAL NOT NULL,
      buys_5m INTEGER NOT NULL,
      sells_5m INTEGER NOT NULL,
      age_minutes REAL NOT NULL,
      price_change_5m_pct REAL NOT NULL,
      previous_high_market_cap_usd REAL,
      sell_impact_pct REAL,
      sell_quote_status TEXT NOT NULL DEFAULT 'pending',
      sell_quote_note TEXT,
      sell_quote_checked_at INTEGER,
      risk_flags_json TEXT NOT NULL DEFAULT '[]',
      safety_status TEXT NOT NULL DEFAULT 'pending',
      observed_at INTEGER NOT NULL,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS candidates_observed_idx ON candidates(observed_at DESC);
    CREATE INDEX IF NOT EXISTS candidates_source_idx ON candidates(source);
    CREATE TABLE IF NOT EXISTS candidate_snapshots (
      id INTEGER PRIMARY KEY,
      mint TEXT NOT NULL REFERENCES candidates(mint) ON DELETE CASCADE,
      captured_at INTEGER NOT NULL,
      market_cap_usd REAL NOT NULL,
      liquidity_usd REAL NOT NULL,
      volume_5m_usd REAL NOT NULL,
      price_change_5m_pct REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS candidate_snapshots_mint_idx
      ON candidate_snapshots(mint, captured_at DESC);
    """)
    migrate_candidate_columns(conn)
    seed_sample_candidates(conn)
    return conn


def migrate_candidate_columns(conn: sqlite3.Connection) -> None:
    """Additive migrations keep existing local research databases usable."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
    additions = {
        "price_usd": "REAL NOT NULL DEFAULT 0",
        "sell_quote_status": "TEXT NOT NULL DEFAULT 'pending'",
        "sell_quote_note": "TEXT",
        "sell_quote_checked_at": "INTEGER",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {definition}")


def seed_sample_candidates(conn: sqlite3.Connection) -> None:
    """Seed fictional candidates once, without replacing future live records."""
    existing = conn.execute("SELECT COUNT(*) FROM candidates WHERE source='sample'").fetchone()[0]
    if existing:
        return

    now = int(time.time())
    for candidate in SAMPLE_CANDIDATES:
        conn.execute(
            """INSERT INTO candidates(
              mint, name, symbol, chain, pair_address, source, setup,
              market_cap_usd, liquidity_usd, volume_5m_usd, buys_5m, sells_5m,
              age_minutes, price_change_5m_pct, previous_high_market_cap_usd,
              sell_impact_pct, risk_flags_json, safety_status, observed_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate["mint"], candidate["name"], candidate["symbol"], candidate["chain"],
                candidate["pair_address"], candidate["source"], candidate["setup"],
                candidate["market_cap_usd"], candidate["liquidity_usd"], candidate["volume_5m_usd"],
                candidate["buys_5m"], candidate["sells_5m"], candidate["age_minutes"],
                candidate["price_change_5m_pct"], candidate["previous_high_market_cap_usd"],
                candidate["sell_impact_pct"], json.dumps(candidate["risk_flags"]),
                candidate["safety_status"], now, now,
            ),
        )
        conn.execute(
            """INSERT INTO candidate_snapshots(
              mint, captured_at, market_cap_usd, liquidity_usd, volume_5m_usd, price_change_5m_pct
            ) VALUES (?,?,?,?,?,?)""",
            (
                candidate["mint"], now, candidate["market_cap_usd"], candidate["liquidity_usd"],
                candidate["volume_5m_usd"], candidate["price_change_5m_pct"],
            ),
        )
    conn.commit()


def number(value: Any) -> float:
    return float(value or 0)


def candidate_assessment(candidate: dict[str, Any]) -> dict[str, Any]:
    """Score a candidate as an explainable research heuristic, never a trade signal."""
    market_cap = number(candidate.get("market_cap_usd"))
    liquidity = number(candidate.get("liquidity_usd"))
    volume_5m = number(candidate.get("volume_5m_usd"))
    buys = int(candidate.get("buys_5m") or 0)
    sells = int(candidate.get("sells_5m") or 0)
    age_minutes = number(candidate.get("age_minutes"))
    price_change = number(candidate.get("price_change_5m_pct"))
    previous_high = number(candidate.get("previous_high_market_cap_usd"))
    raw_sell_impact = candidate.get("sell_impact_pct")
    sell_impact = None if raw_sell_impact is None else number(raw_sell_impact)
    risk_flags = candidate.get("risk_flags") or []
    source = candidate.get("source", "unknown")
    safety_status = candidate.get("safety_status", "pending")
    sell_quote_status = "pass" if source == "sample" and sell_impact is not None else (
        candidate.get("sell_quote_status") or ("pass" if sell_impact is not None else "pending")
    )
    sell_quote_note = candidate.get("sell_quote_note") or ""

    liquidity_ratio = liquidity / market_cap if market_cap else 0
    volume_to_liquidity = volume_5m / liquidity if liquidity else 0
    buy_sell_ratio = buys / sells if sells else float(buys) if buys else 0
    drawdown_pct = (1 - market_cap / previous_high) * 100 if previous_high > market_cap > 0 else 0

    score = 10
    reasons: list[str] = []
    warnings: list[str] = []
    gates: list[dict[str, str]] = []
    hard_failures: list[str] = []

    if 100_000 <= market_cap <= 750_000:
        score += 12
        gates.append({"label": "Market-cap lane", "status": "PASS", "detail": "Inside the $100k–$750k starter range."})
    elif 75_000 <= market_cap <= 3_000_000:
        score += 4
        gates.append({"label": "Market-cap lane", "status": "WATCH", "detail": "Outside the primary lane; review manually."})
    else:
        score -= 10
        gates.append({"label": "Market-cap lane", "status": "FAIL", "detail": "Outside the research range for this first version."})

    if liquidity >= 50_000:
        score += 20
        gates.append({"label": "Liquidity", "status": "PASS", "detail": "At least $50k shown in the fixture."})
    elif liquidity >= 25_000:
        score += 10
        gates.append({"label": "Liquidity", "status": "WATCH", "detail": "Above the $25k minimum, but below the stronger $50k level."})
    else:
        score -= 25
        hard_failures.append("liquidity is below $25k")
        gates.append({"label": "Liquidity", "status": "FAIL", "detail": "Below the $25k minimum for this scanner."})

    if liquidity_ratio >= 0.10:
        score += 10
        reasons.append(f"Liquidity is {liquidity_ratio * 100:.0f}% of market cap, above the 10% target.")
    elif liquidity_ratio >= 0.07:
        score += 3
        warnings.append(f"Liquidity is only {liquidity_ratio * 100:.0f}% of market cap.")
    else:
        score -= 10
        warnings.append(f"Liquidity is only {liquidity_ratio * 100:.0f}% of market cap, which can make exits fragile.")

    if age_minutes >= 5:
        score += 5
        gates.append({"label": "Trading age", "status": "PASS", "detail": f"Observed for {age_minutes:.0f} minutes, beyond the 5-minute wait."})
    else:
        score -= 10
        hard_failures.append("the coin is younger than 5 minutes")
        gates.append({"label": "Trading age", "status": "FAIL", "detail": "Too new for the first observation rule."})

    if volume_to_liquidity >= 0.25:
        score += 8
        reasons.append("Five-minute volume is active relative to displayed liquidity.")
    elif volume_to_liquidity >= 0.10:
        score += 3
    else:
        score -= 4
        warnings.append("Five-minute volume is weak relative to liquidity.")

    if buy_sell_ratio >= 1.5:
        score += 12
        reasons.append(f"Buy pressure is {buy_sell_ratio:.1f}× the sell count over five minutes.")
    elif buy_sell_ratio >= 1:
        score += 4
        reasons.append("Buy and sell counts are balanced, not strongly one-sided.")
    else:
        score -= 8
        warnings.append("Recent sell count exceeds buy count.")

    if sell_quote_status == "no_route":
        hard_failures.append("Jupiter found no route to sell the small test amount")
        gates.append({"label": "Small sell quote", "status": "FAIL", "detail": sell_quote_note or "No sell route was returned for the small test amount."})
    elif sell_quote_status in {"unavailable", "not_configured"}:
        gates.append({"label": "Small sell quote", "status": "PENDING", "detail": sell_quote_note or "Jupiter quote check needs to be retried."})
        warnings.append("A real small-order sell quote is still required before this could be tradeable.")
    elif sell_impact is None:
        gates.append({"label": "Small sell quote", "status": "PENDING", "detail": "Jupiter quote check is not connected yet."})
        warnings.append("A real small-order sell quote is still required before this could be tradeable.")
    elif sell_impact <= 2:
        score += 15
        gates.append({"label": "Small sell quote", "status": "PASS", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})
    elif sell_impact <= 3:
        score += 6
        gates.append({"label": "Small sell quote", "status": "WATCH", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})
    else:
        score -= 25
        hard_failures.append("estimated sell impact is above 3%")
        gates.append({"label": "Small sell quote", "status": "FAIL", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})

    if candidate.get("setup") == "panic_reclaim":
        if previous_high >= 300_000 and 35 <= drawdown_pct <= 70 and price_change >= 3:
            score += 12
            reasons.insert(0, f"Previously reached ${previous_high:,.0f}, then pulled back {drawdown_pct:.0f}% and is reclaiming in the last five minutes.")
        else:
            score -= 8
            warnings.append("The reclaim pattern is incomplete: wait for a proven run, controlled pullback, and renewed buying.")
    elif price_change >= 5:
        score += 8
        reasons.insert(0, f"Price is up {price_change:.1f}% in five minutes while the fixture shows active buyers.")
    else:
        score += 2
        warnings.append("Momentum is present but not yet strong enough for a high-conviction watch.")

    if risk_flags:
        score -= 35
        hard_failures.extend(risk_flags)
        gates.append({"label": "Safety / cluster flags", "status": "FAIL", "detail": "; ".join(risk_flags)})
        warnings.append("A connected-holder or creator-related risk flag must be investigated, not assumed away.")
    elif source == "sample":
        score += 10
        gates.append({"label": "Safety / cluster flags", "status": "PENDING", "detail": "Sample fixture only; live safety checks are not connected."})
        warnings.append("This is fictional sample data, not a completed live safety screen.")
    elif safety_status == "pass":
        score += 10
        gates.append({"label": "Safety / cluster flags", "status": "PASS", "detail": "Live safety check reported no configured hard flags."})
    else:
        gates.append({"label": "Safety / cluster flags", "status": "PENDING", "detail": "Live safety check still required."})
        warnings.append("A live safety check is required before a real coin can be labeled Candidate.")

    score = max(0, min(100, round(score)))
    eligible_for_candidate = source == "sample" or safety_status == "pass"
    if hard_failures:
        status = "avoid"
    elif score >= 75 and eligible_for_candidate:
        status = "candidate"
    elif score >= 40:
        status = "watch"
    else:
        status = "avoid"

    if hard_failures:
        reasons.insert(0, "Avoid: " + "; ".join(hard_failures) + ".")
    elif status == "watch":
        reasons.insert(0, "Watch: it needs more confirmation before it could graduate to a candidate.")

    return {
        "score": score,
        "status": status,
        "reasons": reasons[:4],
        "warnings": warnings[:4],
        "gates": gates,
        "derived": {
            "liquidity_ratio_pct": round(liquidity_ratio * 100, 1),
            "volume_to_liquidity": round(volume_to_liquidity, 2),
            "buy_sell_ratio": round(buy_sell_ratio, 2),
            "drawdown_pct": round(drawdown_pct, 1) if drawdown_pct else None,
        },
    }


def serialize_candidate(row: sqlite3.Row) -> dict[str, Any]:
    candidate = dict(row)
    try:
        candidate["risk_flags"] = json.loads(candidate.pop("risk_flags_json") or "[]")
    except json.JSONDecodeError:
        candidate["risk_flags"] = ["invalid stored risk flag data"]
    candidate.update(candidate_assessment(candidate))
    return candidate


def candidate_feed(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Return explainable candidates, sorted by research status and score."""
    conn = database()
    has_live_records = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM candidates WHERE source != 'sample')"
    ).fetchone()[0]
    where = "WHERE source != 'sample'" if has_live_records else ""
    rows = conn.execute(
        f"SELECT * FROM candidates {where} ORDER BY observed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    candidates = [serialize_candidate(row) for row in rows]
    if status:
        candidates = [candidate for candidate in candidates if candidate["status"] == status]
    candidates.sort(key=lambda candidate: (STATUS_ORDER[candidate["status"]], -candidate["score"], candidate["name"].lower()))

    summary = {name: sum(candidate["status"] == name for candidate in candidates) for name in STATUS_ORDER}
    summary["total"] = len(candidates)
    summary["sample"] = sum(candidate["source"] == "sample" for candidate in candidates)
    summary["live"] = sum(candidate["source"] != "sample" for candidate in candidates)
    is_sample_data = bool(candidates) and all(candidate["source"] == "sample" for candidate in candidates)
    return {
        "generated_at": int(time.time()),
        "is_sample_data": is_sample_data,
        "summary": summary,
        "candidates": candidates,
        "note": (
            "Sample fixtures only. Click Get live Solana pairs to load public DEX Screener data."
            if is_sample_data else
            "DEX Screener public pair data. Every live card is research-only Watch until Jupiter sell quotes and safety checks are connected."
        ),
    }


def candidate_detail(mint: str) -> dict[str, Any] | None:
    conn = database()
    row = conn.execute("SELECT * FROM candidates WHERE mint=?", (mint,)).fetchone()
    if row is None:
        return None
    candidate = serialize_candidate(row)
    history = conn.execute(
        """SELECT captured_at, market_cap_usd, liquidity_usd, volume_5m_usd, price_change_5m_pct
           FROM candidate_snapshots WHERE mint=? ORDER BY captured_at DESC LIMIT 24""",
        (mint,),
    ).fetchall()
    candidate["snapshots"] = [dict(snapshot) for snapshot in reversed(history)]
    return candidate


class UpstreamDataError(RuntimeError):
    """A public data provider could not be reached or returned unusable data."""


def fetch_dexscreener_json(url: str) -> Any:
    """Fetch public DEX Screener data. No account or API key is needed for this POC."""
    request = urllib.request.Request(url, headers={"User-Agent": "MemeTrace/0.1 research dashboard"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"DEX Screener returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach DEX Screener. Try again in a moment.") from exc


def normalize_dexscreener_pair(pair: dict[str, Any], now: int | None = None) -> dict[str, Any] | None:
    """Convert one public pair response into MemeTrace's chain-ready candidate shape."""
    if pair.get("chainId") != "solana":
        return None
    base_token = pair.get("baseToken") or {}
    mint = base_token.get("address")
    if not mint:
        return None
    now = now or int(time.time())
    created_at_ms = number(pair.get("pairCreatedAt"))
    age_minutes = max(0, (now - created_at_ms / 1000) / 60) if created_at_ms else 0
    return {
        "mint": mint,
        "name": base_token.get("name") or "Unknown token",
        "symbol": base_token.get("symbol") or "UNKNOWN",
        "chain": "solana",
        "pair_address": pair.get("pairAddress"),
        "source": "dexscreener",
        "setup": "early_momentum",
        "price_usd": number(pair.get("priceUsd")),
        "market_cap_usd": number(pair.get("marketCap") or pair.get("fdv")),
        "liquidity_usd": number((pair.get("liquidity") or {}).get("usd")),
        "volume_5m_usd": number((pair.get("volume") or {}).get("m5")),
        "buys_5m": int(((pair.get("txns") or {}).get("m5") or {}).get("buys") or 0),
        "sells_5m": int(((pair.get("txns") or {}).get("m5") or {}).get("sells") or 0),
        "age_minutes": age_minutes,
        "price_change_5m_pct": number((pair.get("priceChange") or {}).get("m5")),
        "previous_high_market_cap_usd": None,
        "sell_impact_pct": None,
        "risk_flags": [],
        "safety_status": "pending",
        "observed_at": now,
    }


def passes_dex_discovery_filter(candidate: dict[str, Any]) -> bool:
    """Cheap filter before later safety and quote API calls use any credits."""
    market_cap = number(candidate["market_cap_usd"])
    return (
        DEX_DISCOVERY_MIN_MARKET_CAP_USD <= market_cap <= DEX_DISCOVERY_MAX_MARKET_CAP_USD
        and number(candidate["liquidity_usd"]) >= DEX_DISCOVERY_MIN_LIQUIDITY_USD
        and number(candidate["age_minutes"]) >= DEX_DISCOVERY_MIN_AGE_MINUTES
        and number(candidate["volume_5m_usd"]) >= DEX_DISCOVERY_MIN_5M_VOLUME_USD
        and int(candidate["buys_5m"]) + int(candidate["sells_5m"]) >= 5
    )


def upsert_candidate(candidate: dict[str, Any], conn: sqlite3.Connection) -> None:
    """Store the current observation and append a small historical snapshot."""
    values = (
        candidate["mint"], candidate["name"], candidate["symbol"], candidate["chain"],
        candidate["pair_address"], candidate["source"], candidate["setup"], candidate["price_usd"],
        candidate["market_cap_usd"], candidate["liquidity_usd"], candidate["volume_5m_usd"],
        candidate["buys_5m"], candidate["sells_5m"], candidate["age_minutes"],
        candidate["price_change_5m_pct"], candidate["previous_high_market_cap_usd"],
        candidate["sell_impact_pct"], json.dumps(candidate["risk_flags"]), candidate["safety_status"],
        candidate["observed_at"], candidate["observed_at"],
    )
    conn.execute(
        """INSERT INTO candidates(
          mint, name, symbol, chain, pair_address, source, setup, price_usd, market_cap_usd,
          liquidity_usd, volume_5m_usd, buys_5m, sells_5m, age_minutes,
          price_change_5m_pct, previous_high_market_cap_usd, sell_impact_pct,
          risk_flags_json, safety_status, observed_at, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mint) DO UPDATE SET
          name=excluded.name, symbol=excluded.symbol, chain=excluded.chain,
          pair_address=excluded.pair_address, source=excluded.source, setup=excluded.setup,
          price_usd=excluded.price_usd, market_cap_usd=excluded.market_cap_usd, liquidity_usd=excluded.liquidity_usd,
          volume_5m_usd=excluded.volume_5m_usd, buys_5m=excluded.buys_5m,
          sells_5m=excluded.sells_5m, age_minutes=excluded.age_minutes,
          price_change_5m_pct=excluded.price_change_5m_pct,
          previous_high_market_cap_usd=excluded.previous_high_market_cap_usd,
          risk_flags_json=excluded.risk_flags_json,
          safety_status=excluded.safety_status, observed_at=excluded.observed_at""",
        values,
    )
    conn.execute(
        """INSERT INTO candidate_snapshots(
          mint, captured_at, market_cap_usd, liquidity_usd, volume_5m_usd, price_change_5m_pct
        ) VALUES (?,?,?,?,?,?)""",
        (candidate["mint"], candidate["observed_at"], candidate["market_cap_usd"],
         candidate["liquidity_usd"], candidate["volume_5m_usd"], candidate["price_change_5m_pct"]),
    )


def refresh_dexscreener_candidates(fetcher=fetch_dexscreener_json, now: int | None = None) -> dict[str, Any]:
    """Discover a deliberately small set of live Solana research cards from DEX Screener."""
    now = now or int(time.time())
    profiles = fetcher(f"{DEX_SCREENER_API_BASE}/token-profiles/latest/v1")
    if not isinstance(profiles, list):
        raise UpstreamDataError("DEX Screener sent an unexpected profile response.")
    token_addresses: list[str] = []
    for profile in profiles:
        if not isinstance(profile, dict) or profile.get("chainId") != "solana":
            continue
        address = profile.get("tokenAddress")
        if address and address not in token_addresses:
            token_addresses.append(address)
        if len(token_addresses) >= DEX_DISCOVERY_MAX_TOKENS:
            break

    saved = 0
    pairs_read = 0
    skipped = 0
    conn = database()
    for address in token_addresses:
        pairs = fetcher(f"{DEX_SCREENER_API_BASE}/token-pairs/v1/solana/{urllib.parse.quote(address)}")
        if not isinstance(pairs, list):
            skipped += 1
            continue
        normalized = [normalize_dexscreener_pair(pair, now) for pair in pairs if isinstance(pair, dict)]
        normalized = [candidate for candidate in normalized if candidate is not None]
        pairs_read += len(normalized)
        qualified = [candidate for candidate in normalized if passes_dex_discovery_filter(candidate)]
        if not qualified:
            skipped += 1
            continue
        strongest_pair = max(qualified, key=lambda candidate: number(candidate["liquidity_usd"]))
        upsert_candidate(strongest_pair, conn)
        saved += 1
    conn.commit()
    return {
        "source": "dexscreener",
        "refreshed_at": now,
        "profiles_read": len(profiles),
        "solana_profiles_read": len(token_addresses),
        "pairs_read": pairs_read,
        "records_saved": saved,
        "skipped": skipped,
        "note": "Public DEX Screener pair data only. Saved records remain Watch until sell quotes and safety checks are connected.",
    }


class JupiterNoRouteError(UpstreamDataError):
    """Jupiter did not return a route for a bounded research-only sell quote."""


_last_jupiter_request_at = 0.0


def fetch_jupiter_json(url: str) -> Any:
    """Read Jupiter token metadata or a quote. It never builds or sends a transaction."""
    if not JUPITER_API_KEY:
        raise ValueError("JUPITER_API_KEY is not set. Add a free Jupiter developer key to .env, then restart the server.")
    global _last_jupiter_request_at
    wait_seconds = 1.05 - (time.monotonic() - _last_jupiter_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-api-key": JUPITER_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            _last_jupiter_request_at = time.monotonic()
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _last_jupiter_request_at = time.monotonic()
        if "/swap/v1/quote" in url and exc.code in {400, 404}:
            raise JupiterNoRouteError("Jupiter returned no sell route for this small test amount.") from exc
        raise UpstreamDataError(f"Jupiter returned HTTP {exc.code}. Try the quote check again later.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach Jupiter. Try the quote check again later.") from exc


def jupiter_token_metadata(mint: str, fetcher=fetch_jupiter_json) -> dict[str, Any] | None:
    response = fetcher(f"{JUPITER_API_BASE}/tokens/v2/search?{urllib.parse.urlencode({'query': mint})}")
    records = response.get("data", response) if isinstance(response, dict) else response
    if not isinstance(records, list):
        return None
    return next((record for record in records if record.get("address") == mint), None)


def check_jupiter_sell_quote(candidate: dict[str, Any], fetcher=fetch_jupiter_json) -> dict[str, Any]:
    """Ask Jupiter whether a roughly $5 token sell has a route; no wallet is used."""
    price_usd = number(candidate.get("price_usd"))
    if price_usd <= 0:
        return {"status": "unavailable", "note": "DEX Screener did not provide a usable USD price for this pair.", "impact_pct": None}
    try:
        metadata = jupiter_token_metadata(candidate["mint"], fetcher)
        if not metadata or metadata.get("decimals") is None:
            return {"status": "unavailable", "note": "Jupiter did not return token decimals for this mint.", "impact_pct": None}
        raw_amount = max(1, round((JUPITER_TEST_SELL_USD / price_usd) * (10 ** int(metadata["decimals"]))))
        query = urllib.parse.urlencode({
            "inputMint": candidate["mint"],
            "outputMint": SOL_MINT,
            "amount": raw_amount,
            "slippageBps": 100,
            "restrictIntermediateTokens": "true",
        })
        quote = fetcher(f"{JUPITER_API_BASE}/swap/v1/quote?{query}")
        if not isinstance(quote, dict) or not quote.get("routePlan"):
            return {"status": "no_route", "note": "Jupiter returned no sell route for this small test amount.", "impact_pct": None}
        impact_pct = number(quote.get("priceImpactPct"))
        return {
            "status": "pass",
            "note": f"Jupiter returned a route for a roughly ${JUPITER_TEST_SELL_USD} test sell.",
            "impact_pct": impact_pct,
        }
    except JupiterNoRouteError as exc:
        return {"status": "no_route", "note": str(exc), "impact_pct": None}
    except (UpstreamDataError, ValueError) as exc:
        return {"status": "unavailable", "note": str(exc), "impact_pct": None}


def enrich_jupiter_sellability(fetcher=fetch_jupiter_json, now: int | None = None) -> dict[str, Any]:
    """Quote only a few saved live cards to respect Jupiter's free-tier rate limit."""
    if not JUPITER_API_KEY and fetcher is fetch_jupiter_json:
        return {
            "checked": 0,
            "sellable": 0,
            "no_route": 0,
            "unavailable": 0,
            "note": "Add a free JUPITER_API_KEY to .env, restart the server, then try again.",
        }
    now = now or int(time.time())
    conn = database()
    rows = conn.execute(
        """SELECT * FROM candidates WHERE source != 'sample'
           ORDER BY liquidity_usd DESC, observed_at DESC LIMIT ?""",
        (JUPITER_MAX_QUOTES_PER_CHECK,),
    ).fetchall()
    summary = {"checked": 0, "sellable": 0, "no_route": 0, "unavailable": 0}
    for row in rows:
        result = check_jupiter_sell_quote(dict(row), fetcher)
        conn.execute(
            """UPDATE candidates SET sell_impact_pct=?, sell_quote_status=?, sell_quote_note=?,
               sell_quote_checked_at=? WHERE mint=?""",
            (result["impact_pct"], result["status"], result["note"], now, row["mint"]),
        )
        summary["checked"] += 1
        if result["status"] == "pass":
            summary["sellable"] += 1
        else:
            summary[result["status"]] += 1
    conn.commit()
    summary["note"] = (
        "Quotes are research-only and do not send a transaction. Safety and wallet checks are still pending."
        if summary["checked"] else "No live cards are available yet. Get live Solana pairs first."
    )
    return summary


def fetch_helius_wallet_transactions(address: str) -> list[dict]:
    """Fetch parsed public transactions. Key must remain in local environment only."""
    if not HELIUS_KEY:
        raise ValueError("HELIUS_API_KEY is not set. Create .env locally and start with: set -a; . ./.env; set +a; python3 server.py")
    query = urllib.parse.urlencode({"api-key": HELIUS_KEY, "limit": 100})
    url = f"https://api.helius.xyz/v0/addresses/{urllib.parse.quote(address)}/transactions?{query}"
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ValueError(f"Helius returned HTTP {exc.code}: {detail[:220]}") from exc


def ingest_wallet(address: str) -> dict:
    """Ingest token transfers for a single wallet. A transfer is not automatically a trade."""
    txs = fetch_helius_wallet_transactions(address)
    conn = database()
    inserted = 0
    for tx in txs:
        signature = tx.get("signature")
        timestamp = int(tx.get("timestamp") or time.time())
        if not signature:
            continue
        for transfer in tx.get("tokenTransfers", []):
            mint = transfer.get("mint")
            amount = transfer.get("tokenAmount")
            destination = transfer.get("toUserAccount")
            origin = transfer.get("fromUserAccount")
            if not mint:
                continue
            # We deliberately mark only direction. Later parsing/backtests decide whether it was a swap, airdrop, LP action, etc.
            side = "in" if destination == address else "out" if origin == address else "related"
            conn.execute("INSERT OR IGNORE INTO wallets(address, first_seen) VALUES (?, ?)", (address, timestamp))
            conn.execute("INSERT OR IGNORE INTO tokens(mint, first_seen) VALUES (?, ?)", (mint, timestamp))
            before = conn.total_changes
            conn.execute("""INSERT OR IGNORE INTO trades(signature,wallet,mint,timestamp,side,amount,source)
              VALUES (?,?,?,?,?,?,?)""", (signature, address, mint, timestamp, side, amount, "helius_wallet"))
            inserted += conn.total_changes - before
    conn.execute("INSERT INTO imports(source, created_at, records) VALUES (?,?,?)", ("helius_wallet", int(time.time()), inserted))
    conn.commit()
    return {"wallet": address, "transactions_read": len(txs), "transfer_events_added": inserted,
            "note": "Events are public transfer observations. They are not yet classified as buys/sells."}


def import_events(events: list[dict], source: str = "manual") -> dict:
    """Import bounded, normalized observations for replay/backtesting.

    Required: signature, wallet, mint, timestamp, side. side is buy or sell.
    """
    conn = database()
    added = 0
    for event in events:
        required = ["signature", "wallet", "mint", "timestamp", "side"]
        if any(not event.get(key) for key in required) or event["side"] not in {"buy", "sell"}:
            raise ValueError(f"Invalid event: required {required}; side must be buy or sell")
        conn.execute("INSERT OR IGNORE INTO wallets(address, first_seen) VALUES (?,?)", (event["wallet"], int(event["timestamp"])))
        conn.execute("INSERT OR IGNORE INTO tokens(mint, first_seen) VALUES (?,?)", (event["mint"], int(event["timestamp"])))
        before = conn.total_changes
        conn.execute("""INSERT OR IGNORE INTO trades(signature,wallet,mint,timestamp,side,amount,source)
          VALUES (?,?,?,?,?,?,?)""", (event["signature"], event["wallet"], event["mint"], int(event["timestamp"]), event["side"], event.get("amount"), source))
        added += conn.total_changes - before
    conn.execute("INSERT INTO imports(source, created_at, records) VALUES (?,?,?)", (source, int(time.time()), added))
    conn.commit()
    return {"events_added": added}


def cohort_report(min_shared_launches: int = 3, early_buyer_limit: int = 10) -> dict:
    """Find pairs that repeatedly occur in a token's first N imported BUY records.

    This is a research heuristic; it does not prove that wallet owners are connected.
    """
    conn = database()
    rows = conn.execute("""SELECT mint, wallet, timestamp FROM trades WHERE side='buy'
      ORDER BY mint, timestamp""").fetchall()
    first_buyers: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if len(first_buyers[row["mint"]]) < early_buyer_limit and row["wallet"] not in first_buyers[row["mint"]]:
            first_buyers[row["mint"]].append(row["wallet"])
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for mint, wallets in first_buyers.items():
        for i, left in enumerate(wallets):
            for right in wallets[i + 1:]:
                pairs[tuple(sorted((left, right)))].append(mint)
    candidates = [
      {"wallet_a": pair[0], "wallet_b": pair[1], "shared_launches": len(mints), "mints": mints}
      for pair, mints in pairs.items() if len(mints) >= min_shared_launches
    ]
    return {"method": {"first_buyers_per_token": early_buyer_limit, "min_shared_launches": min_shared_launches},
            "candidates": sorted(candidates, key=lambda x: x["shared_launches"], reverse=True),
            "warning": "Repeated public co-buying is not proof of coordination, identity, intent, or future performance."}


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json_response(self, status: int, payload: dict[str, Any]):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        try:
            if path == "/api/health":
                conn = database()
                table_names = ["wallets", "tokens", "trades", "candidates", "candidate_snapshots"]
                counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in table_names}
                return self.json_response(200, {"ok": True, "helius_configured": bool(HELIUS_KEY), "counts": counts})
            if path == "/api/candidates":
                status = params.get("status", [""])[0].strip().lower() or None
                if status and status not in VALID_STATUSES:
                    return self.json_response(400, {"error": "status must be candidate, watch, or avoid"})
                raw_limit = params.get("limit", ["50"])[0]
                try:
                    limit = max(1, min(100, int(raw_limit)))
                except ValueError:
                    return self.json_response(400, {"error": "limit must be a whole number"})
                return self.json_response(200, candidate_feed(status=status, limit=limit))
            if path.startswith("/api/candidates/"):
                mint = urllib.parse.unquote(path.removeprefix("/api/candidates/")).strip()
                if not mint:
                    return self.json_response(400, {"error": "candidate mint is required"})
                candidate = candidate_detail(mint)
                if candidate is None:
                    return self.json_response(404, {"error": "candidate not found"})
                return self.json_response(200, candidate)
            if path == "/api/cohorts":
                return self.json_response(200, cohort_report())
            if path == "/api/ingest-wallet":
                address = params.get("address", [""])[0].strip()
                if not address:
                    return self.json_response(400, {"error": "address query parameter is required"})
                return self.json_response(200, ingest_wallet(address))
        except ValueError as exc:
            return self.json_response(400, {"error": str(exc)})
        except Exception as exc:  # avoids leaking a stack trace in local browser output
            return self.json_response(500, {"error": f"Unexpected server error: {exc}"})
        return super().do_GET()

    def do_POST(self):
        path, _, _ = self.path.partition("?")
        try:
            if path == "/api/candidates/refresh":
                return self.json_response(200, refresh_dexscreener_candidates())
            if path == "/api/candidates/quote-check":
                return self.json_response(200, enrich_jupiter_sellability())
        except UpstreamDataError as exc:
            return self.json_response(502, {"error": str(exc)})
        except Exception as exc:  # avoids leaking a stack trace in local browser output
            return self.json_response(500, {"error": f"Unexpected server error: {exc}"})
        return self.json_response(404, {"error": "API endpoint not found"})


if __name__ == "__main__":
    database().close()
    print("MemeTrace running at http://127.0.0.1:8080")
    print("API: /api/health  /api/candidates  POST /api/candidates/refresh  POST /api/candidates/quote-check  /api/cohorts")
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
