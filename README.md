# MemeTrace

MemeTrace is a personal, Solana-first memecoin research dashboard. Its goal is
to find a small list of **qualified candidates** and explain the evidence and
risks behind each card.

It is not an auto-buy bot, does not connect a wallet, and does not make trading
recommendations.

## What works now

The first product feature is a local **Qualified Candidates** feed:

- Stores chain-ready candidate records and market snapshots in SQLite.
- Scores each record using transparent, editable rules.
- Labels it as `Candidate`, `Watch`, or `Avoid`.
- Shows why it appeared, the hard risk gates, and what still needs checking.
- Provides a small local API for the dashboard.

The initial cards are fictional sample fixtures. Click **Get live Solana pairs**
to replace them with a deliberately bounded multi-source discovery pass:
DEX Screener, Solana Tracker's latest/trending/graduated feeds, and CoinGecko's
new-pools plus one-hour trending-pools feeds. The existing Solana Tracker and
CoinGecko keys enable the latter two sources; no additional account is required.

After the first live scan, MemeTrace never replaces an empty result with sample
coins: no card means no fresh Solana pair currently passed the live-motion rules.

With all four optional keys configured, **Run full research scan** performs that
discovery pass and every bounded research gate in sequence, then gives one
plain-English final summary. It remains research-only: it never connects a
wallet, creates a transaction, or buys a token.

Live cards are deliberately limited to `Watch`: a Jupiter sell route is useful
evidence but a token-safety screen is still required. The app therefore cannot
turn public discovery data into a trade prompt.

## Run it

This starter uses only Python's standard library.

```bash
cd memetrace-poc
python3 server.py
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080).

### Optional: enable real sell-route checks

The **Check sell routes** button uses Jupiter's public quote and token-metadata
APIs. It never connects a wallet, creates a transaction, signs anything, or
spends funds. Jupiter requires a free developer API key for the token metadata
needed to calculate a roughly $5 token sell in atomic units. If a fresh token is
not indexed in Jupiter's metadata yet, MemeTrace uses the existing Helius key to
read public mint decimals, then retries the quote.

1. Create a free key in the [Jupiter developer portal](https://developers.jup.ag/portal).
2. In the project folder, copy `.env.example` to a new file named `.env`.
3. Replace only the value after `JUPITER_API_KEY=` in `.env`.
4. Stop and restart `python3 server.py` (on Windows, use `py server.py`).

Do not paste the key into ChatGPT or commit `.env` to GitHub. The check uses at
most three saved live cards per click to respect the free API rate limit.

### Optional: enable token safety checks

The **Check token safety** button sends only cards that already have a Jupiter
sell route to Solana Tracker. It records the provider's point-in-time risk score
and flags such as freeze/mint authority, liquidity danger, bundlers, insiders,
and developer holdings. It does not identify real people and does not send a
transaction.

Add a free Solana Tracker key after `SOLANA_TRACKER_API_KEY=` in the same `.env`
file, then restart the server. Tokens with any provider `danger` flag, a rugged
status, or a risk score of 7+ are marked `Avoid`; clean cards still wait for the
Helius wallet-evidence check.

### Optional: enable public wallet evidence

The **Check wallet evidence** button sends only route-confirmed, Tracker-clean
cards to Helius. It reads public asset authority/creator metadata and a bounded
recent transaction history for the observed address. Active mint/freeze authority
is a hard flag. Token transfers from an observed creator/authority address are
shown as unclassified evidence, not proof of selling, identity, coordination, or
intent.

Add a free Helius key after `HELIUS_API_KEY=` in `.env`, then restart the server.

### Optional: cross-check pool data

The **Cross-check market data** button sends only cards that passed the sell,
token-safety, and wallet-evidence gates to CoinGecko's on-chain Demo API. It
compares the DEX Screener price/liquidity snapshot with CoinGecko's pool data.
More than 10% price difference or 40% liquidity difference keeps the card at
`Watch`, rather than declaring either source “wrong.”

Add a free CoinGecko Demo key after `COINGECKO_DEMO_API_KEY=` in `.env`, then
restart the server. A live `Candidate` requires this final cross-check too.

## Test it

```bash
python3 -m unittest -v
```

## Local API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Confirms local database setup and counts saved records. |
| `GET /api/candidates` | Returns the ranked candidate feed. |
| `POST /api/candidates/full-scan` | Runs DEX Screener discovery, then the bounded Jupiter, Solana Tracker, Helius, and CoinGecko checks in order and returns one research-only final summary. |
| `POST /api/candidates/refresh` | Pulls bounded qualifying Solana cards from DEX Screener, Solana Tracker, and CoinGecko new pools. |
| `POST /api/candidates/quote-check` | Checks up to three saved live cards for a roughly $5 Jupiter sell route; never sends a transaction. |
| `POST /api/candidates/safety-check` | Sends route-confirmed cards to Solana Tracker and records point-in-time token-risk evidence. |
| `POST /api/candidates/wallet-check` | Sends Tracker-clean cards to Helius for bounded public authority and transfer evidence. |
| `POST /api/candidates/crosscheck` | Compares final-card pool data with CoinGecko's on-chain Demo API. |
| `GET /api/candidates?status=watch` | Filters the feed to one label. |
| `GET /api/candidates/<mint>` | Returns one candidate and its saved snapshots. |
| `GET /api/cohorts` | Existing public early-buyer co-buy research report. |
| `GET /api/ingest-wallet?address=...` | Existing bounded public Helius wallet-history import. |

## Candidate rules in this first version

A real candidate will eventually need to pass:

- Primary market-cap lane: roughly $70k–$750k.
- Fresh momentum lane: 10 minutes to 48 hours old, +3% to +25% in five minutes.
- At least $25k liquidity to be observed; $50k+ and at least 15% of market cap are required for `Candidate`.
- At least $2k genuine five-minute volume, ten recent swaps, and 1.3× buy/sell pressure.
- Five-minute volume must be 10%–200% of liquidity; extreme turnover is kept out as possible manipulation.
- A small live sell quote below the configured price-impact threshold.
- No configured token, creator, or linked-holder risk flags.

Live records whose safety check is still pending can be shown as `Watch`, but
cannot become a `Candidate`. Sample fixtures are the only temporary exception,
and the dashboard labels them clearly.

## Current discovery rules

The multi-source refresh combines a small DEX Screener shortlist, Solana Tracker
latest/trending/graduated token records, and two CoinGecko new-pool pages. Tracker
is used to discover token addresses, while DEX Screener supplies the genuine
five-minute price, volume, and swap data used by the live filter. It deduplicates
mints and keeps pairs that are 10 minutes to 48 hours old with roughly $70k–$3m
market cap, $25k liquidity, $2k genuine five-minute volume, at least ten recent
swaps, at least 1.3× as many buys as sells, and +3% to +25% five-minute price
movement. Live cards expire from the active feed after 15 minutes unless a new
scan finds them again. These are discovery filters, not quality or safety proof.
If nothing passes, the completed scan now reports how many current pair snapshots
were checked and the most common near-miss gates, rather than implying that no
moving coins exist anywhere.

## Strict risk policy

For a live card to become `Candidate`, it must also pass the research pipeline:

- Jupiter returns a small sell route with no more than 2% estimated price impact.
- Solana Tracker returns no danger flag, a risk score of 3/10 or below, top-10
  holder concentration of 15% or below, snipers at 10% or below, possible
  insiders at 5% or below, bundled-wallet holdings at 5% or below, and developer
  holdings at 1% or below.
- Active mint/freeze authority is an `Avoid`. LP, bonding-curve, suspicious-volume,
  dynamic-fee, and concentration warnings keep a card at `Watch` or `Avoid`
  according to severity.
- Helius must return public creator/authority metadata with no active authority and
  no observed recent token outflow from that address. Missing metadata or outflows
  remain `Watch`; they are evidence, not proof of identity or selling intent.
- CoinGecko must broadly match the DEX Screener price and liquidity snapshot.

These rules are intentionally strict and will often return no cards. They reduce
obvious risks; they do not predict profit or make a token safe.

## Next build step

Create the three free API keys locally, then run the full research pipeline from
the dashboard. Twitter/X and automated bubble-map data remain later modules.

## Safety rules

- Public-chain research only.
- No wallet connection, transaction signing, auto-buying, or copy-trading.
- Treat linked-wallet findings as unverified evidence, never proof of identity
  or intent.
- Never put API keys, seed phrases, private keys, or passwords in Git.
