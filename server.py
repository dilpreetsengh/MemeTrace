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
from copy import deepcopy
from datetime import datetime, timezone
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
DEX_DISCOVERY_MAX_TOKENS = 30
DEX_DISCOVERY_MIN_MARKET_CAP_USD = 70_000
DEX_DISCOVERY_MAX_MARKET_CAP_USD = 3_000_000
DEX_DISCOVERY_MIN_LIQUIDITY_USD = 25_000
DEX_DISCOVERY_MIN_AGE_MINUTES = 10
DEX_DISCOVERY_MAX_AGE_MINUTES = 48 * 60
DEX_DISCOVERY_MIN_5M_VOLUME_USD = 2_000
DEX_DISCOVERY_MIN_5M_PRICE_CHANGE_PCT = 3
DEX_DISCOVERY_MAX_5M_PRICE_CHANGE_PCT = 25
DEX_DISCOVERY_MIN_5M_SWAPS = 10
DEX_DISCOVERY_MIN_BUY_SELL_RATIO = 1.30
MIN_CANDIDATE_LIQUIDITY_USD = 50_000
MIN_CANDIDATE_LIQUIDITY_RATIO = 0.15
MIN_VOLUME_TO_LIQUIDITY = 0.10
MAX_VOLUME_TO_LIQUIDITY = 2.00
HARD_MAX_VOLUME_TO_LIQUIDITY = 5.00
MAX_TRACKER_RISK_SCORE = 3
MAX_TOP10_HOLDER_PCT = 15
MAX_SNIPER_PCT = 10
MAX_INSIDER_PCT = 5
MAX_BUNDLER_PCT = 5
MAX_DEVELOPER_PCT = 1
LIVE_CANDIDATE_TTL_SECONDS = 15 * 60
JUPITER_API_BASE = "https://api.jup.ag"
JUPITER_TEST_SELL_USD = 5
JUPITER_MAX_QUOTES_PER_CHECK = 3
SOL_MINT = "So11111111111111111111111111111111111111112"
SOLANA_TRACKER_API_BASE = "https://data.solanatracker.io"
SOLANA_TRACKER_API_KEY = os.getenv("SOLANA_TRACKER_API_KEY", "")
SOLANA_TRACKER_MAX_CHECKS = 3
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_DEMO_API_KEY = os.getenv("COINGECKO_DEMO_API_KEY", "")
COINGECKO_MAX_CHECKS = 3
SOLANA_TRACKER_DISCOVERY_PATHS = (
    "tokens/latest",
    "tokens/trending/1h",
    "tokens/multi/graduated",
)
SOLANA_TRACKER_DISCOVERY_MAX_TOKENS = 30
COINGECKO_DISCOVERY_PAGES = 2
COINGECKO_TRENDING_DURATION = "1h"
RESEARCH_FILTERS_PATH = ROOT / "research_filters.json"

# These are deliberately visible and editable from the local dashboard.  They
# are research preferences, not safety guarantees or trade instructions.
DEFAULT_RESEARCH_FILTERS: dict[str, dict[str, Any]] = {
    "market_cap": {"enabled": True, "min": 70_000, "max": 3_000_000},
    "liquidity": {"enabled": True, "min": 25_000},
    "age": {"enabled": True, "min": 10, "max": 48 * 60},
    "volume_5m": {"enabled": True, "min": 2_000},
    "swaps_5m": {"enabled": True, "min": 10},
    "buy_sell_ratio": {"enabled": True, "min": 1.30},
    "price_change_5m": {"enabled": True, "min": 3, "max": 25},
    "candidate_market_cap": {"enabled": True, "min": 70_000, "max": 750_000},
    "candidate_liquidity": {"enabled": True, "min": 50_000},
    "liquidity_ratio": {"enabled": True, "min_pct": 15, "hard_min_pct": 10},
    "volume_to_liquidity": {"enabled": True, "min": 0.10, "max": 2.0, "hard_max": 5.0},
    "sell_impact": {"enabled": True, "max_pct": 2.0, "hard_max_pct": 3.0},
    "tracker_risk_score": {"enabled": True, "max": 3.0, "hard_max": 6.0},
    "top10_holders": {"enabled": True, "max_pct": 15.0},
    "snipers": {"enabled": True, "max_pct": 10.0, "hard_max_pct": 20.0},
    "insiders": {"enabled": True, "max_pct": 5.0, "hard_max_pct": 10.0},
    "bundlers": {"enabled": True, "max_pct": 5.0, "hard_max_pct": 15.0},
    "developer_holdings": {"enabled": True, "max_pct": 1.0, "hard_max_pct": 5.0},
    "require_sell_route": {"enabled": True},
    "require_market_crosscheck": {"enabled": True},
    "active_authority": {"enabled": True},
    "creator_activity": {"enabled": True},
}


def filter_settings() -> dict[str, dict[str, Any]]:
    """Return a copy so callers cannot accidentally alter the active local policy."""
    return deepcopy(ACTIVE_RESEARCH_FILTERS)


def normalize_research_filters(raw: Any) -> dict[str, dict[str, Any]]:
    """Accept only documented local research controls and validate their ranges."""
    if not isinstance(raw, dict):
        raise ValueError("Research filters must be a JSON object.")
    normalized = deepcopy(DEFAULT_RESEARCH_FILTERS)
    for group, defaults in DEFAULT_RESEARCH_FILTERS.items():
        supplied = raw.get(group, {})
        if not isinstance(supplied, dict):
            continue
        for key, default in defaults.items():
            value = supplied.get(key, default)
            if key == "enabled":
                if not isinstance(value, bool):
                    raise ValueError(f"{group}.enabled must be true or false.")
                normalized[group][key] = value
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{group}.{key} must be a number.")
            if value < 0 or value > 1_000_000_000:
                raise ValueError(f"{group}.{key} is outside the allowed research range.")
            normalized[group][key] = float(value)

    ordered_ranges = (
        ("market_cap", "min", "max"), ("age", "min", "max"),
        ("price_change_5m", "min", "max"),
        ("candidate_market_cap", "min", "max"),
        ("volume_to_liquidity", "min", "max"),
        ("sell_impact", "max_pct", "hard_max_pct"),
        ("tracker_risk_score", "max", "hard_max"),
        ("snipers", "max_pct", "hard_max_pct"),
        ("insiders", "max_pct", "hard_max_pct"),
        ("bundlers", "max_pct", "hard_max_pct"),
        ("developer_holdings", "max_pct", "hard_max_pct"),
    )
    for group, lower, upper in ordered_ranges:
        if normalized[group][lower] > normalized[group][upper]:
            raise ValueError(f"{group}: the lower limit cannot be higher than the upper limit.")
    if normalized["liquidity_ratio"]["hard_min_pct"] > normalized["liquidity_ratio"]["min_pct"]:
        raise ValueError("liquidity_ratio: hard minimum cannot be higher than Candidate minimum.")
    return normalized


def load_research_filters() -> dict[str, dict[str, Any]]:
    if not RESEARCH_FILTERS_PATH.exists():
        return deepcopy(DEFAULT_RESEARCH_FILTERS)
    try:
        return normalize_research_filters(json.loads(RESEARCH_FILTERS_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        # A damaged local preference file should never stop the research server.
        return deepcopy(DEFAULT_RESEARCH_FILTERS)


def save_research_filters(raw: Any) -> dict[str, dict[str, Any]]:
    global ACTIVE_RESEARCH_FILTERS
    ACTIVE_RESEARCH_FILTERS = normalize_research_filters(raw)
    RESEARCH_FILTERS_PATH.write_text(json.dumps(ACTIVE_RESEARCH_FILTERS, indent=2) + "\n", encoding="utf-8")
    return filter_settings()


def reset_research_filters() -> dict[str, dict[str, Any]]:
    global ACTIVE_RESEARCH_FILTERS
    ACTIVE_RESEARCH_FILTERS = deepcopy(DEFAULT_RESEARCH_FILTERS)
    try:
        RESEARCH_FILTERS_PATH.unlink()
    except FileNotFoundError:
        pass
    return filter_settings()


ACTIVE_RESEARCH_FILTERS = load_research_filters()

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
        "volume_5m_usd": 7_100, "buys_5m": 50, "sells_5m": 34, "age_minutes": 12,
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
      safety_note TEXT,
      safety_score REAL,
      safety_checked_at INTEGER,
      risk_evidence_json TEXT NOT NULL DEFAULT '{}',
      creator_address TEXT,
      wallet_evidence_json TEXT NOT NULL DEFAULT '{}',
      wallet_status TEXT NOT NULL DEFAULT 'pending',
      wallet_checked_at INTEGER,
      crosscheck_status TEXT NOT NULL DEFAULT 'pending',
      crosscheck_note TEXT,
      crosscheck_price_usd REAL,
      crosscheck_liquidity_usd REAL,
      crosscheck_checked_at INTEGER,
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
        "safety_note": "TEXT",
        "safety_score": "REAL",
        "safety_checked_at": "INTEGER",
        "risk_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
        "creator_address": "TEXT",
        "wallet_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
        "wallet_status": "TEXT NOT NULL DEFAULT 'pending'",
        "wallet_checked_at": "INTEGER",
        "crosscheck_status": "TEXT NOT NULL DEFAULT 'pending'",
        "crosscheck_note": "TEXT",
        "crosscheck_price_usd": "REAL",
        "crosscheck_liquidity_usd": "REAL",
        "crosscheck_checked_at": "INTEGER",
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
    settings = ACTIVE_RESEARCH_FILTERS
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
    safety_note = candidate.get("safety_note") or ""
    sell_quote_status = "pass" if source == "sample" and sell_impact is not None else (
        candidate.get("sell_quote_status") or ("pass" if sell_impact is not None else "pending")
    )
    sell_quote_note = candidate.get("sell_quote_note") or ""
    crosscheck_status = "pass" if source == "sample" else candidate.get("crosscheck_status", "pending")
    crosscheck_note = candidate.get("crosscheck_note") or ""

    liquidity_ratio = liquidity / market_cap if market_cap else 0
    volume_to_liquidity = volume_5m / liquidity if liquidity else 0
    buy_sell_ratio = buys / sells if sells else float(buys) if buys else 0
    drawdown_pct = (1 - market_cap / previous_high) * 100 if previous_high > market_cap > 0 else 0

    score = 10
    reasons: list[str] = []
    warnings: list[str] = []
    gates: list[dict[str, str]] = []
    hard_failures: list[str] = []

    market_cap_filter = settings["candidate_market_cap"]
    market_cap_confirmed = not market_cap_filter["enabled"] or market_cap_filter["min"] <= market_cap <= market_cap_filter["max"]
    if not market_cap_filter["enabled"]:
        score += 2
        gates.append({"label": "Market-cap lane", "status": "OFF", "detail": "Turned off in Research filters; market cap is shown for your review."})
    elif market_cap_filter["min"] <= market_cap <= market_cap_filter["max"]:
        score += 12
        gates.append({"label": "Market-cap lane", "status": "PASS", "detail": f"Inside your ${market_cap_filter['min'] / 1000:.0f}k–${market_cap_filter['max'] / 1000:.0f}k Candidate lane."})
    elif settings["market_cap"]["enabled"] and settings["market_cap"]["min"] <= market_cap <= settings["market_cap"]["max"]:
        score += 4
        gates.append({"label": "Market-cap lane", "status": "WATCH", "detail": "Outside the primary lane; review manually."})
    else:
        score -= 10
        hard_failures.append("market cap is outside your active research range")
        gates.append({"label": "Market-cap lane", "status": "FAIL", "detail": "Outside your active research range."})

    candidate_liquidity = settings["candidate_liquidity"]
    discovery_liquidity = settings["liquidity"]
    liquidity_confirmed = not candidate_liquidity["enabled"] or liquidity >= candidate_liquidity["min"]
    if not candidate_liquidity["enabled"]:
        score += 2
        gates.append({"label": "Liquidity", "status": "OFF", "detail": "Candidate-liquidity limit is turned off; shown for manual review."})
    elif liquidity_confirmed:
        score += 20
        gates.append({"label": "Liquidity", "status": "PASS", "detail": f"At least ${candidate_liquidity['min'] / 1000:.0f}k shown in the pool."})
    elif not discovery_liquidity["enabled"] or liquidity >= discovery_liquidity["min"]:
        score += 10
        gates.append({"label": "Liquidity", "status": "WATCH", "detail": f"Above the discovery minimum, but below your ${candidate_liquidity['min'] / 1000:.0f}k Candidate level."})
    else:
        score -= 25
        hard_failures.append(f"liquidity is below ${discovery_liquidity['min'] / 1000:.0f}k")
        gates.append({"label": "Liquidity", "status": "FAIL", "detail": "Below your active discovery minimum."})

    liquidity_ratio_filter = settings["liquidity_ratio"]
    liquidity_ratio_confirmed = not liquidity_ratio_filter["enabled"] or liquidity_ratio >= liquidity_ratio_filter["min_pct"] / 100
    if not liquidity_ratio_filter["enabled"]:
        score += 2
        reasons.append(f"Liquidity is {liquidity_ratio * 100:.0f}% of market cap (ratio filter turned off).")
    elif liquidity_ratio_confirmed:
        score += 10
        reasons.append(f"Liquidity is {liquidity_ratio * 100:.0f}% of market cap, above your {liquidity_ratio_filter['min_pct']:.0f}% target.")
    elif liquidity_ratio >= liquidity_ratio_filter["hard_min_pct"] / 100:
        score += 3
        warnings.append(f"Liquidity is {liquidity_ratio * 100:.0f}% of market cap, below your {liquidity_ratio_filter['min_pct']:.0f}% Candidate target.")
    else:
        score -= 10
        hard_failures.append(f"liquidity is below {liquidity_ratio_filter['hard_min_pct']:.0f}% of market cap")
        warnings.append(f"Liquidity is only {liquidity_ratio * 100:.0f}% of market cap, which can make exits fragile.")

    age_filter = settings["age"]
    age_confirmed = not age_filter["enabled"] or age_filter["min"] <= age_minutes <= age_filter["max"]
    if not age_filter["enabled"]:
        score += 1
        gates.append({"label": "Trading age", "status": "OFF", "detail": "Age filter is turned off; age is shown for manual review."})
    elif age_confirmed:
        score += 5
        gates.append({"label": "Trading age", "status": "PASS", "detail": f"Observed for {age_minutes:.0f} minutes, inside your {age_filter['min']:.0f}-minute to {age_filter['max'] / 60:.0f}-hour window."})
    else:
        score -= 10
        hard_failures.append("the coin is outside your active fresh-age window")
        gates.append({"label": "Trading age", "status": "FAIL", "detail": "Too new for confirmation or too old for this fresh-momentum lane."})

    turnover_filter = settings["volume_to_liquidity"]
    volume_quality_confirmed = not turnover_filter["enabled"] or turnover_filter["min"] <= volume_to_liquidity <= turnover_filter["max"]
    if not turnover_filter["enabled"]:
        score += 1
        warnings.append("Five-minute volume/liquidity ratio filter is turned off; inspect turnover manually.")
    elif volume_quality_confirmed:
        score += 8
        reasons.append("Five-minute volume is active without being extreme relative to displayed liquidity.")
    elif turnover_filter["min"] <= volume_to_liquidity <= turnover_filter["hard_max"]:
        score += 3
        warnings.append("Five-minute volume is unusually high relative to liquidity; treat it as possible manipulation until confirmed.")
    elif volume_to_liquidity > turnover_filter["hard_max"]:
        score -= 20
        hard_failures.append("five-minute volume is extremely high relative to liquidity")
        warnings.append("Extreme turnover can be wash trading or a short-lived spike.")
    else:
        score -= 4
        hard_failures.append("five-minute volume is weak relative to liquidity")
        warnings.append("Five-minute volume is weak relative to liquidity.")

    buy_pressure_filter = settings["buy_sell_ratio"]
    buy_pressure_confirmed = not buy_pressure_filter["enabled"] or buy_sell_ratio >= buy_pressure_filter["min"]
    if not buy_pressure_filter["enabled"]:
        score += 1
        warnings.append("Buy/sell pressure filter is turned off; counts are shown for manual review.")
    elif buy_sell_ratio >= max(1.5, buy_pressure_filter["min"]):
        score += 12
        reasons.append(f"Buy pressure is {buy_sell_ratio:.1f}× the sell count over five minutes.")
    elif buy_pressure_confirmed:
        score += 4
        reasons.append(f"Buy pressure is {buy_sell_ratio:.1f}× the sell count over five minutes.")
    else:
        score -= 8
        hard_failures.append(f"buy pressure is below your {buy_pressure_filter['min']:.2f}× minimum")
        warnings.append("Recent sell count exceeds buy count.")

    sell_filter = settings["sell_impact"]
    require_sell_route = settings["require_sell_route"]["enabled"]
    sell_route_confirmed = not require_sell_route
    if sell_quote_status == "no_route" and require_sell_route:
        hard_failures.append("Jupiter found no route to sell the small test amount")
        gates.append({"label": "Small sell quote", "status": "FAIL", "detail": sell_quote_note or "No sell route was returned for the small test amount."})
    elif sell_quote_status in {"unavailable", "not_configured"} and require_sell_route:
        gates.append({"label": "Small sell quote", "status": "PENDING", "detail": sell_quote_note or "Jupiter quote check needs to be retried."})
        warnings.append("A real small-order sell quote is still required before this could be tradeable.")
    elif sell_impact is None and require_sell_route:
        gates.append({"label": "Small sell quote", "status": "PENDING", "detail": "Jupiter quote check is not connected yet."})
        warnings.append("A real small-order sell quote is still required before this could be tradeable.")
    elif not require_sell_route:
        gates.append({"label": "Small sell quote", "status": "OFF", "detail": "Sell-route requirement is turned off; any returned quote is displayed as research evidence."})
    elif not sell_filter["enabled"]:
        sell_route_confirmed = sell_impact is not None and sell_quote_status == "pass"
        gates.append({"label": "Small sell quote", "status": "OFF", "detail": "Sell-impact limit is turned off; review the returned quote manually."})
    elif sell_impact <= sell_filter["max_pct"]:
        sell_route_confirmed = True
        score += 15
        gates.append({"label": "Small sell quote", "status": "PASS", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})
    elif sell_impact <= sell_filter["hard_max_pct"]:
        sell_route_confirmed = True
        score += 6
        gates.append({"label": "Small sell quote", "status": "WATCH", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})
    else:
        score -= 25
        hard_failures.append(f"estimated sell impact is above {sell_filter['hard_max_pct']:g}%")
        gates.append({"label": "Small sell quote", "status": "FAIL", "detail": f"Jupiter estimates {sell_impact:.2f}% price impact for a ${JUPITER_TEST_SELL_USD} test sell."})

    require_crosscheck = settings["require_market_crosscheck"]["enabled"]
    crosscheck_confirmed = not require_crosscheck
    if not require_crosscheck:
        gates.append({"label": "Second market-data source", "status": "OFF", "detail": "Second-source confirmation is turned off; any returned comparison is still displayed."})
    elif crosscheck_status == "pass":
        crosscheck_confirmed = True
        if source != "sample":
            score += 6
        gates.append({"label": "Second market-data source", "status": "PASS", "detail": crosscheck_note or "CoinGecko pool data was broadly consistent."})
    elif crosscheck_status == "mismatch":
        gates.append({"label": "Second market-data source", "status": "WATCH", "detail": crosscheck_note or "Price or liquidity differed between providers."})
        warnings.append("Cross-provider market data differs; wait for it to settle before treating this as a candidate.")
    else:
        gates.append({"label": "Second market-data source", "status": "PENDING", "detail": crosscheck_note or "CoinGecko pool cross-check is still required."})
        warnings.append("A second market-data source is still required before a real coin can be labeled Candidate.")

    if candidate.get("setup") == "panic_reclaim":
        if previous_high >= 300_000 and 35 <= drawdown_pct <= 70 and price_change >= 3:
            score += 12
            reasons.insert(0, f"Previously reached ${previous_high:,.0f}, then pulled back {drawdown_pct:.0f}% and is reclaiming in the last five minutes.")
        else:
            score -= 8
            warnings.append("The reclaim pattern is incomplete: wait for a proven run, controlled pullback, and renewed buying.")
    motion_filter = settings["price_change_5m"]
    motion_confirmed = not motion_filter["enabled"] or motion_filter["min"] <= price_change <= motion_filter["max"]
    if not motion_filter["enabled"]:
        score += 1
        warnings.append("Five-minute momentum filter is turned off; price movement is shown for manual review.")
    elif price_change > motion_filter["max"]:
        hard_failures.append("five-minute price move is too extended for a fresh entry")
        warnings.append("The price is already vertical; this scanner does not treat a late spike as a Candidate.")
    elif price_change >= max(5, motion_filter["min"]):
        score += 8
        reasons.insert(0, f"Price is up {price_change:.1f}% in five minutes with active buyers.")
    else:
        score += 2
        warnings.append("Momentum is present but not yet inside the preferred fresh-entry range.")

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
        gates.append({"label": "Safety / cluster flags", "status": "PASS", "detail": safety_note or "Live safety and wallet checks reported no configured hard flags."})
    elif safety_status == "tracker_pass":
        gates.append({"label": "Safety / cluster flags", "status": "WATCH", "detail": safety_note or "Solana Tracker found no configured hard flags; Helius wallet evidence is still pending."})
        warnings.append("A public creator/wallet evidence check is still required before a real coin can be labeled Candidate.")
    elif safety_status == "watch":
        gates.append({"label": "Safety / cluster flags", "status": "WATCH", "detail": safety_note or "A non-fatal holder, liquidity, or creator-distribution warning needs review."})
        warnings.append("A risk warning blocks Candidate status until it clears on a later scan.")
    else:
        gates.append({"label": "Safety / cluster flags", "status": "PENDING", "detail": "Live safety check still required."})
        warnings.append("A live safety check is required before a real coin can be labeled Candidate.")

    momentum_confirmed = source == "sample" or motion_confirmed
    if source != "sample" and not momentum_confirmed:
        warnings.append("Five-minute price momentum is outside the fresh-entry range for a Candidate.")

    score = max(0, min(100, round(score)))
    safety_confirmed = source == "sample" or (safety_status == "pass" and crosscheck_confirmed)
    eligible_for_candidate = safety_confirmed and momentum_confirmed and liquidity_confirmed and liquidity_ratio_confirmed \
        and volume_quality_confirmed and buy_pressure_confirmed and market_cap_confirmed and age_confirmed and sell_route_confirmed
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
    try:
        candidate["risk_evidence"] = json.loads(candidate.pop("risk_evidence_json") or "{}")
    except json.JSONDecodeError:
        candidate["risk_evidence"] = {"error": "invalid stored risk evidence"}
    try:
        candidate["wallet_evidence"] = json.loads(candidate.pop("wallet_evidence_json") or "{}")
    except json.JSONDecodeError:
        candidate["wallet_evidence"] = {"error": "invalid stored wallet evidence"}
    candidate.update(candidate_assessment(candidate))
    return candidate


def candidate_feed(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Return explainable candidates, sorted by research status and score."""
    conn = database()
    fresh_cutoff = int(time.time()) - LIVE_CANDIDATE_TTL_SECONDS
    has_live_records = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM candidates WHERE source != 'sample' AND observed_at >= ?)", (fresh_cutoff,)
    ).fetchone()[0]
    has_started_live_scan = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM imports WHERE source='live_scan')"
    ).fetchone()[0]
    if has_live_records:
        where = "WHERE source != 'sample' AND observed_at >= ?"
        query_params: tuple[Any, ...] = (fresh_cutoff, limit)
    elif has_started_live_scan:
        # Do not replace an empty live scan with fictional starter cards.
        where = "WHERE 1=0"
        query_params = (limit,)
    else:
        where = ""
        query_params = (limit,)
    rows = conn.execute(
        f"SELECT * FROM candidates {where} ORDER BY observed_at DESC LIMIT ?", query_params
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
            "No fresh Solana pairs meet the live-motion rules in this scan. Try another scan later."
            if not candidates else
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


def discovery_filter_failures(candidate: dict[str, Any]) -> list[str]:
    """Explain exactly why a current pair did not enter the expensive risk pipeline."""
    settings = ACTIVE_RESEARCH_FILTERS
    market_cap = number(candidate.get("market_cap_usd"))
    liquidity = number(candidate.get("liquidity_usd"))
    age_minutes = number(candidate.get("age_minutes"))
    volume = number(candidate.get("volume_5m_usd"))
    buys = int(candidate.get("buys_5m") or 0)
    sells = int(candidate.get("sells_5m") or 0)
    price_change = number(candidate.get("price_change_5m_pct"))
    buy_sell_ratio = buys / sells if sells else float(buys) if buys else 0
    failures: list[str] = []
    if settings["market_cap"]["enabled"] and not settings["market_cap"]["min"] <= market_cap <= settings["market_cap"]["max"]:
        failures.append("market cap")
    if settings["liquidity"]["enabled"] and liquidity < settings["liquidity"]["min"]:
        failures.append("liquidity")
    if settings["age"]["enabled"] and not settings["age"]["min"] <= age_minutes <= settings["age"]["max"]:
        failures.append("age")
    if settings["volume_5m"]["enabled"] and volume < settings["volume_5m"]["min"]:
        failures.append("five-minute volume")
    if settings["swaps_5m"]["enabled"] and buys + sells < settings["swaps_5m"]["min"]:
        failures.append("recent swap count")
    if settings["buy_sell_ratio"]["enabled"] and buy_sell_ratio < settings["buy_sell_ratio"]["min"]:
        failures.append("buy pressure")
    if settings["price_change_5m"]["enabled"] and price_change < settings["price_change_5m"]["min"]:
        failures.append("five-minute momentum")
    elif settings["price_change_5m"]["enabled"] and price_change > settings["price_change_5m"]["max"]:
        failures.append("overextended five-minute spike")
    return failures


def discovery_diagnostics(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose bounded feed coverage and common rejection reasons instead of hiding an empty scan."""
    failures: defaultdict[str, int] = defaultdict(int)
    passed = 0
    for candidate in candidates:
        reasons = discovery_filter_failures(candidate)
        if reasons:
            for reason in reasons:
                failures[reason] += 1
        else:
            passed += 1
    return {
        "pairs_seen": len(candidates),
        "passed_starter_filter": passed,
        "rejected": len(candidates) - passed,
        "common_failures": [
            {"label": label, "count": count}
            for label, count in sorted(failures.items(), key=lambda item: (-item[1], item[0]))[:3]
        ],
    }


def passes_dex_discovery_filter(candidate: dict[str, Any]) -> bool:
    """Keep only fresh Solana pairs with live upward movement before later API checks."""
    return not discovery_filter_failures(candidate)


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
          sell_impact_pct=NULL, sell_quote_status='pending', sell_quote_note=NULL, sell_quote_checked_at=NULL,
          safety_note=NULL, safety_score=NULL, safety_checked_at=NULL, risk_evidence_json='{}',
          creator_address=NULL, wallet_evidence_json='{}', wallet_status='pending', wallet_checked_at=NULL,
          crosscheck_status='pending', crosscheck_note=NULL, crosscheck_price_usd=NULL,
          crosscheck_liquidity_usd=NULL, crosscheck_checked_at=NULL,
          risk_flags_json='[]', safety_status='pending', observed_at=excluded.observed_at""",
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
    observed_candidates: list[dict[str, Any]] = []
    conn = database()
    for address in token_addresses:
        pairs = fetcher(f"{DEX_SCREENER_API_BASE}/token-pairs/v1/solana/{urllib.parse.quote(address)}")
        if not isinstance(pairs, list):
            skipped += 1
            continue
        normalized = [normalize_dexscreener_pair(pair, now) for pair in pairs if isinstance(pair, dict)]
        normalized = [candidate for candidate in normalized if candidate is not None]
        observed_candidates.extend(normalized)
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
        "diagnostics": discovery_diagnostics(observed_candidates),
        "note": "Public DEX Screener pair data only. Saved records remain Watch until sell quotes and safety checks are connected.",
    }


def fetch_solana_tracker_discovery(path: str) -> Any:
    """Read a bounded token-discovery feed with the existing Solana Tracker key."""
    if not SOLANA_TRACKER_API_KEY:
        raise ValueError("SOLANA_TRACKER_API_KEY is not set.")
    request = urllib.request.Request(
        f"{SOLANA_TRACKER_API_BASE}/{path}",
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-api-key": SOLANA_TRACKER_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"Solana Tracker discovery returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach Solana Tracker discovery. Try again in a moment.") from exc


def fetch_coingecko_new_pools(page: int) -> Any:
    """Read one bounded page of new pools with the existing CoinGecko Demo key."""
    if not COINGECKO_DEMO_API_KEY:
        raise ValueError("COINGECKO_DEMO_API_KEY is not set.")
    query = urllib.parse.urlencode({"include": "base_token", "page": page})
    request = urllib.request.Request(
        f"{COINGECKO_API_BASE}/onchain/networks/new_pools?{query}",
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-cg-demo-api-key": COINGECKO_DEMO_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"CoinGecko discovery returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach CoinGecko new-pools discovery. Try again in a moment.") from exc


def fetch_coingecko_trending_pools() -> Any:
    """Read current one-hour Solana pool momentum with the existing CoinGecko Demo key."""
    if not COINGECKO_DEMO_API_KEY:
        raise ValueError("COINGECKO_DEMO_API_KEY is not set.")
    query = urllib.parse.urlencode({"include": "base_token", "duration": COINGECKO_TRENDING_DURATION})
    request = urllib.request.Request(
        f"{COINGECKO_API_BASE}/onchain/networks/solana/trending_pools?{query}",
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-cg-demo-api-key": COINGECKO_DEMO_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"CoinGecko trending-pools discovery returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach CoinGecko trending-pools discovery. Try again in a moment.") from exc


def value_at(data: dict[str, Any], *keys: str) -> Any:
    """Read a nested provider field without assuming every new launch has every stat."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def created_age_minutes(value: Any, now: int) -> float:
    if isinstance(value, (int, float)):
        timestamp = float(value) / 1000 if value > 10_000_000_000 else float(value)
        return max(0, (now - timestamp) / 60)
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
            return max(0, (now - timestamp) / 60)
        except ValueError:
            return 0
    return 0


def normalize_solana_tracker_candidate(raw: dict[str, Any], now: int | None = None) -> dict[str, Any] | None:
    """Convert one Tracker discovery record into the shared candidate shape."""
    now = now or int(time.time())
    token = raw.get("token") or raw
    mint = token.get("mint") or token.get("address")
    pools = raw.get("pools") or []
    if not mint or not isinstance(pools, list) or not pools:
        return None
    pool = max(pools, key=lambda item: number(value_at(item, "liquidity", "usd")))
    transactions = pool.get("txns") or {}
    events = raw.get("events") or {}
    return {
        "mint": mint,
        "name": token.get("name") or "Unknown token",
        "symbol": token.get("symbol") or "UNKNOWN",
        "chain": "solana",
        "pair_address": pool.get("poolId") or pool.get("address"),
        "source": "solanatracker",
        "setup": "early_momentum",
        "price_usd": number(value_at(pool, "price", "usd")),
        "market_cap_usd": number(value_at(pool, "marketCap", "usd") or pool.get("marketCapUsd")),
        "liquidity_usd": number(value_at(pool, "liquidity", "usd")),
        "volume_5m_usd": number(value_at(events, "5m", "volume")),
        "buys_5m": int(transactions.get("buys") or 0),
        "sells_5m": int(transactions.get("sells") or 0),
        "age_minutes": created_age_minutes(pool.get("createdAt") or value_at(token, "creation", "created_time"), now),
        "price_change_5m_pct": number(value_at(events, "5m", "priceChangePercentage")),
        "previous_high_market_cap_usd": None,
        "sell_impact_pct": None,
        "risk_flags": [],
        "safety_status": "pending",
        "observed_at": now,
    }


def normalize_coingecko_candidate(pool: dict[str, Any], included: dict[str, dict[str, Any]], now: int | None = None) -> dict[str, Any] | None:
    """Convert a Solana CoinGecko new-pool response into the shared candidate shape."""
    now = now or int(time.time())
    relationships = pool.get("relationships") or {}
    if value_at(relationships, "network", "data", "id") != "solana":
        return None
    base_token_id = value_at(relationships, "base_token", "data", "id")
    token = included.get(base_token_id or "", {})
    token_attributes = token.get("attributes") or {}
    attributes = pool.get("attributes") or {}
    mint = token_attributes.get("address")
    if not mint or not attributes.get("address"):
        return None
    transactions = value_at(attributes, "transactions", "m5") or {}
    return {
        "mint": mint,
        "name": token_attributes.get("name") or "Unknown token",
        "symbol": token_attributes.get("symbol") or "UNKNOWN",
        "chain": "solana",
        "pair_address": attributes["address"],
        "source": "coingecko",
        "setup": "early_momentum",
        "price_usd": number(attributes.get("base_token_price_usd")),
        "market_cap_usd": number(attributes.get("market_cap_usd") or attributes.get("fdv_usd")),
        "liquidity_usd": number(attributes.get("reserve_in_usd")),
        "volume_5m_usd": number(value_at(attributes, "volume_usd", "m5")),
        "buys_5m": int(transactions.get("buys") or 0),
        "sells_5m": int(transactions.get("sells") or 0),
        "age_minutes": created_age_minutes(attributes.get("pool_created_at"), now),
        "price_change_5m_pct": number(value_at(attributes, "price_change_percentage", "m5")),
        "previous_high_market_cap_usd": None,
        "sell_impact_pct": None,
        "risk_flags": [],
        "safety_status": "pending",
        "observed_at": now,
    }


def save_discovery_candidates(candidates: list[dict[str, Any]]) -> int:
    """Keep the deepest-liquidity record for each mint before the later gated checks."""
    strongest: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not passes_dex_discovery_filter(candidate):
            continue
        existing = strongest.get(candidate["mint"])
        if existing is None or number(candidate["liquidity_usd"]) > number(existing["liquidity_usd"]):
            strongest[candidate["mint"]] = candidate
    conn = database()
    for candidate in strongest.values():
        upsert_candidate(candidate, conn)
    conn.commit()
    return len(strongest)


def mark_live_candidates_stale() -> None:
    """Keep history in SQLite, but remove old scan cards from the active research feed."""
    conn = database()
    conn.execute("UPDATE candidates SET observed_at=0 WHERE source != 'sample'")
    conn.execute(
        "INSERT INTO imports(source, created_at, records) VALUES (?,?,?)",
        ("live_scan", int(time.time()), 0),
    )
    conn.commit()


def refresh_multi_source_candidates(
    dex_refresh=refresh_dexscreener_candidates,
    tracker_fetcher=fetch_solana_tracker_discovery,
    coingecko_fetcher=fetch_coingecko_new_pools,
    coingecko_trending_fetcher=fetch_coingecko_trending_pools,
    dex_pair_fetcher=fetch_dexscreener_json,
    now: int | None = None,
) -> dict[str, Any]:
    """Merge bounded public DEX, Tracker, and CoinGecko discovery feeds before scoring."""
    now = now or int(time.time())
    providers: dict[str, dict[str, Any]] = {}
    mark_live_candidates_stale()

    try:
        dex = dex_refresh(now=now)
        providers["dexscreener"] = {"records_saved": dex["records_saved"], "status": "ok", "diagnostics": dex.get("diagnostics", {})}
    except (UpstreamDataError, ValueError) as exc:
        providers["dexscreener"] = {"records_saved": 0, "status": "unavailable", "note": str(exc)}

    tracker_mints: list[str] = []
    for path in SOLANA_TRACKER_DISCOVERY_PATHS:
        try:
            response = tracker_fetcher(path)
            records = response if isinstance(response, list) else response.get("data") or response.get("tokens") or []
            for item in records:
                if not isinstance(item, dict):
                    continue
                token = item.get("token") or item
                mint = token.get("mint") or token.get("address")
                if mint and mint not in tracker_mints:
                    tracker_mints.append(mint)
                if len(tracker_mints) >= SOLANA_TRACKER_DISCOVERY_MAX_TOKENS:
                    break
            if len(tracker_mints) >= SOLANA_TRACKER_DISCOVERY_MAX_TOKENS:
                break
        except (UpstreamDataError, ValueError) as exc:
            providers["solana_tracker"] = {"records_saved": 0, "status": "partial", "note": str(exc)}
            break
    else:
        providers["solana_tracker"] = {"records_saved": 0, "status": "ok"}

    tracker_candidates: list[dict[str, Any]] = []
    for mint in tracker_mints:
        try:
            pairs = dex_pair_fetcher(f"{DEX_SCREENER_API_BASE}/token-pairs/v1/solana/{urllib.parse.quote(mint)}")
            if not isinstance(pairs, list):
                continue
            tracker_candidates.extend(
                candidate for candidate in (normalize_dexscreener_pair(pair, now) for pair in pairs if isinstance(pair, dict))
                if candidate is not None
            )
        except UpstreamDataError:
            continue
    if "solana_tracker" not in providers:
        providers["solana_tracker"] = {"records_saved": 0, "status": "ok"}
    tracker_saved = save_discovery_candidates(tracker_candidates)
    providers["solana_tracker"]["records_saved"] = tracker_saved
    providers["solana_tracker"]["diagnostics"] = discovery_diagnostics(tracker_candidates)

    coingecko_candidates: list[dict[str, Any]] = []
    for page in range(1, COINGECKO_DISCOVERY_PAGES + 1):
        try:
            response = coingecko_fetcher(page)
            included = {item.get("id"): item for item in response.get("included") or [] if isinstance(item, dict) and item.get("id")}
            normalized = [normalize_coingecko_candidate(item, included, now) for item in response.get("data") or [] if isinstance(item, dict)]
            coingecko_candidates.extend(item for item in normalized if item is not None)
        except (UpstreamDataError, ValueError) as exc:
            providers["coingecko"] = {"records_saved": 0, "status": "partial", "note": str(exc)}
            break
    else:
        providers["coingecko"] = {"records_saved": 0, "status": "ok"}

    try:
        response = coingecko_trending_fetcher()
        included = {item.get("id"): item for item in response.get("included") or [] if isinstance(item, dict) and item.get("id")}
        normalized = [normalize_coingecko_candidate(item, included, now) for item in response.get("data") or [] if isinstance(item, dict)]
        coingecko_candidates.extend(item for item in normalized if item is not None)
        providers["coingecko"]["trending_pools_seen"] = len(normalized)
    except (UpstreamDataError, ValueError) as exc:
        providers["coingecko"]["status"] = "partial"
        providers["coingecko"]["note"] = str(exc)

    coingecko_saved = save_discovery_candidates(coingecko_candidates)
    providers["coingecko"]["records_saved"] = coingecko_saved
    providers["coingecko"]["diagnostics"] = discovery_diagnostics(coingecko_candidates)

    total_saved = sum(number(provider.get("records_saved")) for provider in providers.values())
    failure_counts: defaultdict[str, int] = defaultdict(int)
    pairs_seen = 0
    passed_starter_filter = 0
    for provider in providers.values():
        diagnostics = provider.get("diagnostics") or {}
        pairs_seen += int(diagnostics.get("pairs_seen") or 0)
        passed_starter_filter += int(diagnostics.get("passed_starter_filter") or 0)
        for failure in diagnostics.get("common_failures") or []:
            failure_counts[str(failure.get("label"))] += int(failure.get("count") or 0)
    coverage = {
        "pairs_seen": pairs_seen,
        "passed_starter_filter": passed_starter_filter,
        "rejected": max(0, pairs_seen - passed_starter_filter),
        "common_failures": [
            {"label": label, "count": count}
            for label, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
        ],
    }
    return {
        "source": "multi_source",
        "refreshed_at": now,
        "records_saved": int(total_saved),
        "providers": providers,
        "coverage": coverage,
        "note": "Bounded DEX Screener, Solana Tracker, CoinGecko new-pool, and CoinGecko one-hour trending-pool discovery. Cards still must pass sellability and safety gates.",
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


def fetch_helius_mint_decimals(mint: str) -> int:
    """Use public Solana mint data as a fallback when a fresh token is not indexed by Jupiter yet."""
    if not HELIUS_KEY:
        raise ValueError("HELIUS_API_KEY is not set, so mint decimals could not be checked.")
    url = f"https://mainnet.helius-rpc.com/?{urllib.parse.urlencode({'api-key': HELIUS_KEY})}"
    body = json.dumps({"jsonrpc": "2.0", "id": "memetrace-decimals", "method": "getTokenSupply", "params": [mint]}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "MemeTrace/0.1 research dashboard"})
    try:
        wait_for_helius_rate_limit()
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        decimals = value_at(data, "result", "value", "decimals")
        if decimals is None:
            raise UpstreamDataError("Helius did not return mint decimals for this token.")
        return int(decimals)
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"Helius decimal fallback returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("HELIUS_API_KEY"):
            raise
        raise UpstreamDataError("Could not read public mint decimals from Helius.") from exc


def check_jupiter_sell_quote(candidate: dict[str, Any], fetcher=fetch_jupiter_json,
                             decimal_fallback=fetch_helius_mint_decimals) -> dict[str, Any]:
    """Ask Jupiter whether a roughly $5 token sell has a route; no wallet is used."""
    price_usd = number(candidate.get("price_usd"))
    if price_usd <= 0:
        return {"status": "unavailable", "note": "DEX Screener did not provide a usable USD price for this pair.", "impact_pct": None}
    try:
        metadata = jupiter_token_metadata(candidate["mint"], fetcher)
        decimals = metadata.get("decimals") if metadata else None
        decimal_source = "Jupiter"
        if decimals is None:
            decimals = decimal_fallback(candidate["mint"])
            decimal_source = "Helius fallback"
        raw_amount = max(1, round((JUPITER_TEST_SELL_USD / price_usd) * (10 ** int(decimals))))
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
            "note": f"Jupiter returned a route for a roughly ${JUPITER_TEST_SELL_USD} test sell ({decimal_source} decimals).",
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
        """SELECT * FROM candidates WHERE source != 'sample' AND observed_at >= ?
           ORDER BY liquidity_usd DESC, observed_at DESC LIMIT ?""",
        (now - LIVE_CANDIDATE_TTL_SECONDS, JUPITER_MAX_QUOTES_PER_CHECK),
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


def fetch_solana_tracker_token(mint: str) -> dict[str, Any]:
    """Fetch the token risk object from Solana Tracker's Data API."""
    if not SOLANA_TRACKER_API_KEY:
        raise ValueError("SOLANA_TRACKER_API_KEY is not set. Add a free Solana Tracker key to .env, then restart the server.")
    request = urllib.request.Request(
        f"{SOLANA_TRACKER_API_BASE}/tokens/{urllib.parse.quote(mint)}",
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-api-key": SOLANA_TRACKER_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise UpstreamDataError("Solana Tracker sent an unexpected token response.")
            return data
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"Solana Tracker returned HTTP {exc.code}. Try the safety check again later.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach Solana Tracker. Try the safety check again later.") from exc


def tracker_percentage(risk: dict[str, Any], key: str) -> float:
    """Read a percentage from a documented Tracker risk sub-object without guessing identities."""
    value = risk.get(key) or {}
    if isinstance(value, dict):
        return number(value.get("totalPercentage") if value.get("totalPercentage") is not None else value.get("percentage"))
    return number(value)


def interpret_solana_tracker_risk(token: dict[str, Any]) -> dict[str, Any]:
    """Apply MemeTrace's explicit holder/liquidity policy to public Tracker risk evidence."""
    settings = ACTIVE_RESEARCH_FILTERS
    risk = token.get("risk")
    if not isinstance(risk, dict) or not risk:
        return {"status": "unavailable", "score": None, "flags": [], "evidence": {},
                "note": "Solana Tracker did not return a usable risk object for this token."}

    raw_risks = risk.get("risks") or []
    danger_flags: list[str] = []
    watch_flags: list[str] = []
    provider_warnings: list[str] = []
    lp_or_curve_status = "No LP or bonding-curve risk was returned"
    for item in raw_risks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Unnamed risk")
        description = str(item.get("description") or "")
        level = str(item.get("level") or "").lower()
        lower_name = name.lower()
        display = f"Solana Tracker: {name}" + (f" — {description}" if description else "")
        if "liquidity" in lower_name or "lp" in lower_name or "bonding curve" in lower_name:
            lp_or_curve_status = name
        if level == "danger":
            danger_flags.append(display)
        elif level == "warning":
            provider_warnings.append(name)
            if any(term in lower_name for term in ("incomplete bonding curve", "transitioning", "suspicious volume", "price decrease", "dynamic fee", "liquidity", "lp")):
                watch_flags.append(display)

    score = number(risk.get("score"))
    top10_pct = number(risk.get("top10"))
    sniper_pct = tracker_percentage(risk, "snipers")
    insider_pct = tracker_percentage(risk, "insiders")
    bundler_pct = tracker_percentage(risk, "bundlers")
    developer_pct = tracker_percentage(risk, "dev")
    evidence = {
        "risk_score": score,
        "top_10_holder_pct": top10_pct,
        "sniper_holder_pct": sniper_pct,
        "insider_holder_pct": insider_pct,
        "bundler_holder_pct": bundler_pct,
        "developer_holder_pct": developer_pct,
        "lp_or_curve_status": lp_or_curve_status,
        "provider_warnings": provider_warnings[:6],
    }

    if risk.get("rugged"):
        danger_flags.insert(0, "Solana Tracker: token is marked rugged (no usable liquidity reported)")
    score_filter = settings["tracker_risk_score"]
    if score_filter["enabled"]:
        if score > score_filter["hard_max"]:
            danger_flags.append(f"Solana Tracker: risk score {score:.1f}/10 is above your {score_filter['hard_max']:.1f}/10 hard limit")
        elif score > score_filter["max"]:
            watch_flags.append(f"Solana Tracker: risk score {score:.1f}/10 is above your {score_filter['max']:.1f}/10 Candidate limit")

    top10_filter = settings["top10_holders"]
    if top10_filter["enabled"] and top10_pct > top10_filter["max_pct"]:
        danger_flags.append(f"Solana Tracker: top 10 holders control {top10_pct:.1f}% (limit {top10_filter['max_pct']:.1f}%)")

    def apply_two_level_percentage(setting_name: str, observed: float, label: str) -> None:
        policy = settings[setting_name]
        if not policy["enabled"]:
            return
        if observed > policy["hard_max_pct"]:
            danger_flags.append(f"Solana Tracker: {label} {observed:.1f}% (hard limit {policy['hard_max_pct']:.1f}%)")
        elif observed > policy["max_pct"]:
            watch_flags.append(f"Solana Tracker: {label} {observed:.1f}% (Candidate limit {policy['max_pct']:.1f}%)")

    apply_two_level_percentage("snipers", sniper_pct, "early snipers hold")
    apply_two_level_percentage("insiders", insider_pct, "possible insiders hold")
    apply_two_level_percentage("bundlers", bundler_pct, "bundled wallets hold")
    apply_two_level_percentage("developer_holdings", developer_pct, "developer holdings are")

    status = "avoid" if danger_flags else "watch" if watch_flags else "tracker_pass"
    note_parts = [f"Solana Tracker risk score {score:.1f}/10"]
    if watch_flags:
        note_parts.append("Warnings: " + "; ".join(watch_flags[:3]))
    elif provider_warnings:
        note_parts.append("Provider warnings: " + ", ".join(provider_warnings[:3]))
    else:
        note_parts.append("No configured holder, LP, or volume warnings returned")
    return {"status": status, "score": score, "flags": danger_flags[:6], "evidence": evidence,
            "note": ". ".join(note_parts) + "."}


def enrich_solana_tracker_risk(fetcher=fetch_solana_tracker_token, now: int | None = None) -> dict[str, Any]:
    """Check a few sellable cards only, preserving the free API quota for research."""
    if not SOLANA_TRACKER_API_KEY and fetcher is fetch_solana_tracker_token:
        return {"checked": 0, "clean": 0, "watch": 0, "flagged": 0, "unavailable": 0,
                "note": "Add a free SOLANA_TRACKER_API_KEY to .env, restart the server, then try again."}
    now = now or int(time.time())
    conn = database()
    rows = conn.execute(
        """SELECT * FROM candidates WHERE source != 'sample' AND observed_at >= ? AND sell_quote_status='pass'
           ORDER BY liquidity_usd DESC, observed_at DESC LIMIT ?""",
        (now - LIVE_CANDIDATE_TTL_SECONDS, SOLANA_TRACKER_MAX_CHECKS),
    ).fetchall()
    summary = {"checked": 0, "clean": 0, "watch": 0, "flagged": 0, "unavailable": 0}
    for row in rows:
        try:
            result = interpret_solana_tracker_risk(fetcher(row["mint"]))
        except (UpstreamDataError, ValueError) as exc:
            result = {"status": "unavailable", "score": None, "flags": [], "evidence": {}, "note": str(exc)}
        conn.execute(
            """UPDATE candidates SET risk_flags_json=?, safety_status=?, safety_note=?, safety_score=?, risk_evidence_json=?,
               safety_checked_at=? WHERE mint=?""",
            (json.dumps(result["flags"]), result["status"], result["note"], result["score"], json.dumps(result["evidence"]), now, row["mint"]),
        )
        summary["checked"] += 1
        if result["status"] == "tracker_pass":
            summary["clean"] += 1
        elif result["status"] == "watch":
            summary["watch"] += 1
        elif result["status"] == "avoid":
            summary["flagged"] += 1
        else:
            summary["unavailable"] += 1
    conn.commit()
    summary["note"] = (
        "Risk results are point-in-time research evidence. Helius creator and wallet evidence is still pending."
        if summary["checked"] else "Check sell routes first; only cards with a confirmed route are sent to Solana Tracker."
    )
    return summary


_last_helius_request_at = 0.0


def wait_for_helius_rate_limit() -> None:
    """Free Helius enhanced/DAS APIs allow only a small bounded request rate."""
    global _last_helius_request_at
    wait_seconds = 0.55 - (time.monotonic() - _last_helius_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_helius_request_at = time.monotonic()


def fetch_helius_asset(mint: str) -> dict[str, Any]:
    """Read public DAS token metadata; this is never a wallet connection."""
    if not HELIUS_KEY:
        raise ValueError("HELIUS_API_KEY is not set. Add a free Helius key to .env, then restart the server.")
    url = f"https://mainnet.helius-rpc.com/?{urllib.parse.urlencode({'api-key': HELIUS_KEY})}"
    body = json.dumps({"jsonrpc": "2.0", "id": "memetrace", "method": "getAsset", "params": {"id": mint}}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "MemeTrace/0.1 research dashboard"})
    try:
        wait_for_helius_rate_limit()
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("result"), dict):
            raise UpstreamDataError("Helius did not return usable asset metadata for this mint.")
        return payload["result"]
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"Helius returned HTTP {exc.code}. Try the wallet check again later.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach Helius. Try the wallet check again later.") from exc


def fetch_helius_wallet_transactions(address: str) -> list[dict]:
    """Fetch parsed public transactions. Key must remain in local environment only."""
    if not HELIUS_KEY:
        raise ValueError("HELIUS_API_KEY is not set. Create .env locally and start with: set -a; . ./.env; set +a; python3 server.py")
    query = urllib.parse.urlencode({"api-key": HELIUS_KEY, "limit": 100})
    url = f"https://api.helius.xyz/v0/addresses/{urllib.parse.quote(address)}/transactions?{query}"
    try:
        wait_for_helius_rate_limit()
        with urllib.request.urlopen(url, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ValueError(f"Helius returned HTTP {exc.code}: {detail[:220]}") from exc


def interpret_helius_wallet_evidence(asset: dict[str, Any], transactions: list[dict], mint: str) -> dict[str, Any]:
    """Summarize public metadata and transfers without claiming a person's identity."""
    settings = ACTIVE_RESEARCH_FILTERS
    token_info = asset.get("token_info") or {}
    creators = [item.get("address") for item in asset.get("creators") or [] if isinstance(item, dict) and item.get("address")]
    authorities = [item.get("address") for item in asset.get("authorities") or [] if isinstance(item, dict) and item.get("address")]
    creator_address = creators[0] if creators else (authorities[0] if authorities else None)
    static_flags = []
    if settings["active_authority"]["enabled"] and token_info.get("mint_authority"):
        static_flags.append("Helius: mint authority is still present")
    if settings["active_authority"]["enabled"] and token_info.get("freeze_authority"):
        static_flags.append("Helius: freeze authority is still present")
    outgoing = 0
    incoming = 0
    if creator_address:
        for transaction in transactions:
            for transfer in transaction.get("tokenTransfers") or []:
                if transfer.get("mint") != mint:
                    continue
                if transfer.get("fromUserAccount") == creator_address:
                    outgoing += 1
                if transfer.get("toUserAccount") == creator_address:
                    incoming += 1
    evidence = {
        "creator_or_authority": creator_address,
        "creator_addresses_observed": len(creators),
        "authority_addresses_observed": len(authorities),
        "recent_token_outflows_from_observed_address": outgoing,
        "recent_token_inflows_to_observed_address": incoming,
        "warning": "Public address associations and transfers are evidence, not proof of identity, coordination, or intent.",
    }
    activity_note = f"Observed {outgoing} recent token outflow(s) from the public creator/authority address." if creator_address else "No creator/authority address was returned in public asset metadata."
    note = f"Helius checked public asset authority metadata. {activity_note}"
    if static_flags:
        status = "avoid"
    elif settings["creator_activity"]["enabled"] and creator_address is None:
        status = "watch"
        note += " Candidate status is blocked because creator/authority evidence is unavailable."
    elif settings["creator_activity"]["enabled"] and outgoing:
        status = "watch"
        note += " Candidate status is blocked until this distribution evidence is reviewed; transfers do not by themselves prove selling or intent."
    else:
        status = "pass"
    return {"status": status, "flags": static_flags, "note": note, "creator_address": creator_address, "evidence": evidence}


def enrich_helius_wallet_evidence(asset_fetcher=fetch_helius_asset, transaction_fetcher=fetch_helius_wallet_transactions, now: int | None = None) -> dict[str, Any]:
    """Enrich only route-confirmed, token-risk-clean cards with bounded public evidence."""
    if not HELIUS_KEY and asset_fetcher is fetch_helius_asset:
        return {"checked": 0, "clear": 0, "watch": 0, "flagged": 0, "unavailable": 0,
                "note": "Add a free HELIUS_API_KEY to .env, restart the server, then try again."}
    now = now or int(time.time())
    conn = database()
    rows = conn.execute(
        """SELECT * FROM candidates WHERE source != 'sample' AND observed_at >= ? AND sell_quote_status='pass'
           AND safety_status IN ('tracker_pass', 'watch') ORDER BY liquidity_usd DESC, observed_at DESC LIMIT ?""",
        (now - LIVE_CANDIDATE_TTL_SECONDS, SOLANA_TRACKER_MAX_CHECKS),
    ).fetchall()
    summary = {"checked": 0, "clear": 0, "watch": 0, "flagged": 0, "unavailable": 0}
    for row in rows:
        current_flags = json.loads(row["risk_flags_json"] or "[]")
        try:
            asset = asset_fetcher(row["mint"])
            public_address = next((item.get("address") for item in asset.get("creators") or [] if isinstance(item, dict) and item.get("address")), None)
            if public_address is None:
                public_address = next((item.get("address") for item in asset.get("authorities") or [] if isinstance(item, dict) and item.get("address")), None)
            transactions = transaction_fetcher(public_address) if public_address else []
            result = interpret_helius_wallet_evidence(asset, transactions, row["mint"])
        except (UpstreamDataError, ValueError) as exc:
            result = {"status": "unavailable", "flags": [], "note": str(exc), "creator_address": None, "evidence": {}}
        flags = (current_flags + result["flags"])[:4]
        tracker_was_watch = row["safety_status"] == "watch"
        status = "avoid" if result["status"] == "avoid" else "watch" if tracker_was_watch or result["status"] == "watch" else result["status"]
        note = (row["safety_note"] + " " if tracker_was_watch and row["safety_note"] else "") + result["note"]
        conn.execute(
            """UPDATE candidates SET risk_flags_json=?, safety_status=?, safety_note=?, creator_address=?,
               wallet_evidence_json=?, wallet_status=?, wallet_checked_at=? WHERE mint=?""",
            (json.dumps(flags), status, note, result["creator_address"], json.dumps(result["evidence"]), status, now, row["mint"]),
        )
        summary["checked"] += 1
        if status == "pass":
            summary["clear"] += 1
        elif status == "avoid":
            summary["flagged"] += 1
        elif status == "watch":
            summary["watch"] += 1
        else:
            summary["unavailable"] += 1
    conn.commit()
    summary["note"] = (
        "Helius results are public-chain evidence only. CoinGecko pool cross-checking is still pending."
        if summary["checked"] else "Check sell routes and token safety first; only clean cards are sent to Helius."
    )
    return summary


def fetch_coingecko_pool(pair_address: str) -> dict[str, Any]:
    """Fetch one public on-chain pool record from CoinGecko's Demo API."""
    if not COINGECKO_DEMO_API_KEY:
        raise ValueError("COINGECKO_DEMO_API_KEY is not set. Add a free CoinGecko Demo key to .env, then restart the server.")
    url = f"{COINGECKO_API_BASE}/onchain/networks/solana/pools/{urllib.parse.quote(pair_address)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MemeTrace/0.1 research dashboard", "x-cg-demo-api-key": COINGECKO_DEMO_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise UpstreamDataError("CoinGecko did not return usable pool data for this pair.")
        return data["data"]
    except urllib.error.HTTPError as exc:
        raise UpstreamDataError(f"CoinGecko returned HTTP {exc.code}. Try the cross-check again later.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamDataError("Could not reach CoinGecko. Try the cross-check again later.") from exc


def interpret_coingecko_pool(candidate: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    """Compare public pool observations rather than treating either provider as ground truth."""
    attributes = pool.get("attributes") or {}
    gecko_price = number(attributes.get("base_token_price_usd"))
    gecko_liquidity = number(attributes.get("reserve_in_usd"))
    dex_price = number(candidate.get("price_usd"))
    dex_liquidity = number(candidate.get("liquidity_usd"))
    if gecko_price <= 0 or dex_price <= 0:
        return {"status": "unavailable", "price_usd": gecko_price or None, "liquidity_usd": gecko_liquidity or None,
                "note": "One provider did not return a usable USD price for this pool."}
    price_gap = abs(dex_price - gecko_price) / dex_price * 100
    liquidity_gap = abs(dex_liquidity - gecko_liquidity) / dex_liquidity * 100 if dex_liquidity and gecko_liquidity else None
    mismatched = price_gap > 10 or (liquidity_gap is not None and liquidity_gap > 40)
    note = f"DEX Screener vs CoinGecko: price differs {price_gap:.1f}%" + (
        f", liquidity differs {liquidity_gap:.1f}%" if liquidity_gap is not None else ""
    ) + "."
    return {"status": "mismatch" if mismatched else "pass", "price_usd": gecko_price,
            "liquidity_usd": gecko_liquidity or None, "note": note}


def enrich_coingecko_crosscheck(fetcher=fetch_coingecko_pool, now: int | None = None) -> dict[str, Any]:
    """Cross-check only cards that have already passed the first three bounded gates."""
    if not COINGECKO_DEMO_API_KEY and fetcher is fetch_coingecko_pool:
        return {"checked": 0, "consistent": 0, "mismatched": 0, "unavailable": 0,
                "note": "Add a free COINGECKO_DEMO_API_KEY to .env, restart the server, then try again."}
    now = now or int(time.time())
    conn = database()
    rows = conn.execute(
        """SELECT * FROM candidates WHERE source != 'sample' AND observed_at >= ? AND sell_quote_status='pass'
           AND safety_status='pass' ORDER BY liquidity_usd DESC, observed_at DESC LIMIT ?""",
        (now - LIVE_CANDIDATE_TTL_SECONDS, COINGECKO_MAX_CHECKS),
    ).fetchall()
    summary = {"checked": 0, "consistent": 0, "mismatched": 0, "unavailable": 0}
    for row in rows:
        try:
            result = interpret_coingecko_pool(dict(row), fetcher(row["pair_address"]))
        except (UpstreamDataError, ValueError) as exc:
            result = {"status": "unavailable", "price_usd": None, "liquidity_usd": None, "note": str(exc)}
        conn.execute(
            """UPDATE candidates SET crosscheck_status=?, crosscheck_note=?, crosscheck_price_usd=?,
               crosscheck_liquidity_usd=?, crosscheck_checked_at=? WHERE mint=?""",
            (result["status"], result["note"], result["price_usd"], result["liquidity_usd"], now, row["mint"]),
        )
        summary["checked"] += 1
        if result["status"] == "pass":
            summary["consistent"] += 1
        elif result["status"] == "mismatch":
            summary["mismatched"] += 1
        else:
            summary["unavailable"] += 1
    conn.commit()
    summary["note"] = (
        "Cross-checks compare two public snapshots and can differ briefly during fast moves; they are not trade advice."
        if summary["checked"] else "Check sell routes, token safety, and wallet evidence first; only clean cards are sent to CoinGecko."
    )
    return summary


def validate_solana_mint(address: str) -> str:
    """Accept a pasted Solana mint without treating an EVM contract as a Solana token."""
    mint = address.strip()
    if mint.startswith("0x"):
        raise ValueError("That is an EVM 0x contract. Address research is Solana-only in this version.")
    alphabet = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    if not 32 <= len(mint) <= 64 or any(character not in alphabet for character in mint):
        raise ValueError("Paste a valid Solana token mint address.")
    return mint


def research_token_address(
    address: str,
    pair_fetcher=fetch_dexscreener_json,
    now: int | None = None,
    run_deep_checks: bool = True,
) -> dict[str, Any]:
    """Research one pasted Solana mint without requiring it to pass discovery first."""
    mint = validate_solana_mint(address)
    now = now or int(time.time())
    pairs = pair_fetcher(f"{DEX_SCREENER_API_BASE}/token-pairs/v1/solana/{urllib.parse.quote(mint)}")
    if not isinstance(pairs, list):
        raise UpstreamDataError("DEX Screener returned an unexpected token-pair response.")
    candidates = [normalize_dexscreener_pair(pair, now) for pair in pairs if isinstance(pair, dict)]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        raise ValueError("No Solana DEX pair was found for that address yet.")
    selected = max(candidates, key=lambda candidate: number(candidate["liquidity_usd"]))
    selected["source"] = "address_lookup"
    conn = database()
    upsert_candidate(selected, conn)
    conn.commit()

    stages: dict[str, dict[str, Any]] = {}
    if not run_deep_checks:
        return {"candidate": candidate_detail(mint), "stages": stages,
                "note": "Pair data loaded. Deep research checks were not run."}

    if not JUPITER_API_KEY:
        stages["sell_route"] = {"status": "not_configured", "note": "Jupiter key is not configured."}
    else:
        quote = check_jupiter_sell_quote(selected)
        conn.execute(
            """UPDATE candidates SET sell_impact_pct=?, sell_quote_status=?, sell_quote_note=?, sell_quote_checked_at=? WHERE mint=?""",
            (quote["impact_pct"], quote["status"], quote["note"], now, mint),
        )
        conn.commit()
        stages["sell_route"] = {"status": quote["status"], "note": quote["note"]}

    row = conn.execute("SELECT * FROM candidates WHERE mint=?", (mint,)).fetchone()
    if row and row["sell_quote_status"] == "pass" and SOLANA_TRACKER_API_KEY:
        tracker = interpret_solana_tracker_risk(fetch_solana_tracker_token(mint))
        conn.execute(
            """UPDATE candidates SET risk_flags_json=?, safety_status=?, safety_note=?, safety_score=?, risk_evidence_json=?,
               safety_checked_at=? WHERE mint=?""",
            (json.dumps(tracker["flags"]), tracker["status"], tracker["note"], tracker["score"],
             json.dumps(tracker["evidence"]), now, mint),
        )
        conn.commit()
        stages["token_safety"] = {"status": tracker["status"], "note": tracker["note"]}
    elif not SOLANA_TRACKER_API_KEY:
        stages["token_safety"] = {"status": "not_configured", "note": "Solana Tracker key is not configured."}
    else:
        stages["token_safety"] = {"status": "skipped", "note": "A usable Jupiter sell route is required before the safety check."}

    row = conn.execute("SELECT * FROM candidates WHERE mint=?", (mint,)).fetchone()
    if row and row["safety_status"] in {"tracker_pass", "watch"} and HELIUS_KEY:
        current_flags = json.loads(row["risk_flags_json"] or "[]")
        asset = fetch_helius_asset(mint)
        public_address = next((item.get("address") for item in asset.get("creators") or [] if isinstance(item, dict) and item.get("address")), None)
        if public_address is None:
            public_address = next((item.get("address") for item in asset.get("authorities") or [] if isinstance(item, dict) and item.get("address")), None)
        transactions = fetch_helius_wallet_transactions(public_address) if public_address else []
        wallet = interpret_helius_wallet_evidence(asset, transactions, mint)
        safety_status = "avoid" if wallet["status"] == "avoid" else "watch" if row["safety_status"] == "watch" or wallet["status"] == "watch" else wallet["status"]
        safety_note = (row["safety_note"] + " " if row["safety_status"] == "watch" and row["safety_note"] else "") + wallet["note"]
        conn.execute(
            """UPDATE candidates SET risk_flags_json=?, safety_status=?, safety_note=?, creator_address=?,
               wallet_evidence_json=?, wallet_status=?, wallet_checked_at=? WHERE mint=?""",
            (json.dumps((current_flags + wallet["flags"])[:6]), safety_status, safety_note, wallet["creator_address"],
             json.dumps(wallet["evidence"]), safety_status, now, mint),
        )
        conn.commit()
        stages["wallet_evidence"] = {"status": safety_status, "note": wallet["note"]}
    elif not HELIUS_KEY:
        stages["wallet_evidence"] = {"status": "not_configured", "note": "Helius key is not configured."}
    else:
        stages["wallet_evidence"] = {"status": "skipped", "note": "The token did not clear the first safety step."}

    row = conn.execute("SELECT * FROM candidates WHERE mint=?", (mint,)).fetchone()
    if row and row["safety_status"] == "pass" and COINGECKO_DEMO_API_KEY:
        crosscheck = interpret_coingecko_pool(dict(row), fetch_coingecko_pool(row["pair_address"]))
        conn.execute(
            """UPDATE candidates SET crosscheck_status=?, crosscheck_note=?, crosscheck_price_usd=?,
               crosscheck_liquidity_usd=?, crosscheck_checked_at=? WHERE mint=?""",
            (crosscheck["status"], crosscheck["note"], crosscheck["price_usd"], crosscheck["liquidity_usd"], now, mint),
        )
        conn.commit()
        stages["market_crosscheck"] = {"status": crosscheck["status"], "note": crosscheck["note"]}
    elif not COINGECKO_DEMO_API_KEY:
        stages["market_crosscheck"] = {"status": "not_configured", "note": "CoinGecko key is not configured."}
    else:
        stages["market_crosscheck"] = {"status": "skipped", "note": "The token did not clear the wallet-evidence step."}

    candidate = candidate_detail(mint)
    return {
        "candidate": candidate,
        "stages": stages,
        "note": "Address research completed. A result is research evidence, not a buy instruction.",
    }


def build_full_scan_summary(stages: dict[str, dict[str, Any]], feed: dict[str, Any]) -> dict[str, Any]:
    """Explain a completed research scan without turning it into trade advice."""
    discovery = stages["discovery"]
    quotes = stages["sell_routes"]
    safety = stages["safety"]
    wallets = stages["wallet_evidence"]
    crosscheck = stages["market_crosscheck"]
    summary = feed["summary"]

    for stage in (quotes, safety, wallets, crosscheck):
        note = str(stage.get("note") or "")
        if "Add a free" in note and "API_KEY" in note:
            return {
                "headline": "Full scan needs one API key",
                "detail": note,
                "candidate_count": summary["candidate"],
                "watch_count": summary["watch"],
                "avoid_count": summary["avoid"],
                "top_candidate": None,
            }

    if not discovery.get("records_saved"):
        coverage = discovery.get("coverage") or {}
        pairs_seen = int(coverage.get("pairs_seen") or 0)
        failures = coverage.get("common_failures") or []
        failure_summary = ", ".join(
            f"{item.get('label')} ({int(item.get('count') or 0)})" for item in failures
        )
        detail = (
            f"Checked {pairs_seen} current pair snapshots across the bounded discovery feeds. None met every strict starter rule"
            + (f". Most common near-miss gates: {failure_summary}." if failure_summary else ".")
            + " This does not mean no moving coins exist; it means this scan did not find one inside the current research limits."
            if pairs_seen else
            "The discovery providers did not return usable current pair snapshots this scan. Check the provider notes and retry shortly; sample cards are never treated as live results."
        )
        return {
            "headline": "No fresh trending cards this scan",
            "detail": detail,
            "candidate_count": 0,
            "watch_count": 0,
            "avoid_count": 0,
            "top_candidate": None,
        }

    finalists = [card for card in feed["candidates"] if card["status"] == "candidate"]
    if finalists:
        top = finalists[0]
        return {
            "headline": f"Research candidate found: {top['name']} (${top['symbol']})",
            "detail": (
                f"It passed the configured route, safety, public wallet-evidence, and market-data gates "
                f"with a research score of {top['score']}/100. Review the card manually; this is not a buy instruction."
            ),
            "candidate_count": summary["candidate"],
            "watch_count": summary["watch"],
            "avoid_count": summary["avoid"],
            "top_candidate": {"mint": top["mint"], "name": top["name"], "symbol": top["symbol"], "score": top["score"]},
        }

    if not quotes.get("sellable"):
        detail = "No scanned card had a usable small Jupiter sell route, so later checks were skipped to protect API limits."
    elif not safety.get("clean"):
        detail = "Sellable cards were found, but none passed the configured Solana Tracker safety gate."
    elif not wallets.get("clear"):
        detail = "Cards passed the first safety screen, but none cleared the public creator/authority evidence gate."
    elif not crosscheck.get("consistent"):
        detail = "Cards reached the final step, but no pool had a consistent second market-data snapshot yet."
    else:
        detail = "The scan completed, but no card cleared every configured research gate. Review Watch and Avoid cards before scanning again."

    return {
        "headline": "No completed research candidate this scan",
        "detail": detail,
        "candidate_count": summary["candidate"],
        "watch_count": summary["watch"],
        "avoid_count": summary["avoid"],
        "top_candidate": None,
    }


def run_full_research_scan(
    discovery=refresh_multi_source_candidates,
    quote_check=enrich_jupiter_sellability,
    safety_check=enrich_solana_tracker_risk,
    wallet_check=enrich_helius_wallet_evidence,
    market_crosscheck=enrich_coingecko_crosscheck,
    feed_provider=candidate_feed,
) -> dict[str, Any]:
    """Run every bounded, research-only stage once and return one plain-English result."""
    stages = {
        "discovery": discovery(),
        "sell_routes": quote_check(),
        "safety": safety_check(),
        "wallet_evidence": wallet_check(),
        "market_crosscheck": market_crosscheck(),
    }
    feed = feed_provider()
    return {"stages": stages, "summary": build_full_scan_summary(stages, feed)}


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

    def json_body(self) -> dict[str, Any]:
        """Read a tiny JSON request body for local dashboard actions."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Request body length is invalid.") from exc
        if length <= 0 or length > 16_384:
            raise ValueError("A small JSON request body is required.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object.")
        return body

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        try:
            if path == "/api/health":
                conn = database()
                table_names = ["wallets", "tokens", "trades", "candidates", "candidate_snapshots"]
                counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in table_names}
                return self.json_response(200, {"ok": True, "helius_configured": bool(HELIUS_KEY), "counts": counts})
            if path == "/api/research-filters":
                return self.json_response(200, {"filters": filter_settings(), "defaults": deepcopy(DEFAULT_RESEARCH_FILTERS)})
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
            if path == "/api/research-filters":
                body = self.json_body()
                return self.json_response(200, {"filters": save_research_filters(body.get("filters", body))})
            if path == "/api/research-filters/reset":
                return self.json_response(200, {"filters": reset_research_filters(), "note": "Research filters reset to the starter defaults."})
            if path == "/api/candidates/lookup":
                body = self.json_body()
                address = str(body.get("address") or "")
                return self.json_response(200, research_token_address(address))
            if path == "/api/candidates/refresh":
                return self.json_response(200, refresh_multi_source_candidates())
            if path == "/api/candidates/full-scan":
                return self.json_response(200, run_full_research_scan())
            if path == "/api/candidates/quote-check":
                return self.json_response(200, enrich_jupiter_sellability())
            if path == "/api/candidates/safety-check":
                return self.json_response(200, enrich_solana_tracker_risk())
            if path == "/api/candidates/wallet-check":
                return self.json_response(200, enrich_helius_wallet_evidence())
            if path == "/api/candidates/crosscheck":
                return self.json_response(200, enrich_coingecko_crosscheck())
        except UpstreamDataError as exc:
            return self.json_response(502, {"error": str(exc)})
        except ValueError as exc:
            return self.json_response(400, {"error": str(exc)})
        except Exception as exc:  # avoids leaking a stack trace in local browser output
            return self.json_response(500, {"error": f"Unexpected server error: {exc}"})
        return self.json_response(404, {"error": "API endpoint not found"})


if __name__ == "__main__":
    database().close()
    print("MemeTrace running at http://127.0.0.1:8080")
    print("API: /api/health  /api/research-filters  /api/candidates  POST /api/research-filters  POST /api/research-filters/reset  POST /api/candidates/lookup  POST /api/candidates/refresh  POST /api/candidates/full-scan  POST /api/candidates/quote-check  POST /api/candidates/safety-check  POST /api/candidates/wallet-check  POST /api/candidates/crosscheck  /api/cohorts")
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
