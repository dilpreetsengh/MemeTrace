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
in the dashboard to replace them with a deliberately small public DEX Screener
discovery pass. No DEX Screener account or key is needed for this step.

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
needed to calculate a roughly $5 token sell in atomic units.

1. Create a free key in the [Jupiter developer portal](https://developers.jup.ag/portal).
2. In the project folder, copy `.env.example` to a new file named `.env`.
3. Replace only the value after `JUPITER_API_KEY=` in `.env`.
4. Stop and restart `python3 server.py` (on Windows, use `py server.py`).

Do not paste the key into ChatGPT or commit `.env` to GitHub. The check uses at
most three saved live cards per click to respect the free API rate limit.

## Test it

```bash
python3 -m unittest -v
```

## Local API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Confirms local database setup and counts saved records. |
| `GET /api/candidates` | Returns the ranked candidate feed. |
| `POST /api/candidates/refresh` | Pulls a small set of public Solana token profiles and qualifying pairs from DEX Screener. |
| `POST /api/candidates/quote-check` | Checks up to three saved live cards for a roughly $5 Jupiter sell route; never sends a transaction. |
| `GET /api/candidates?status=watch` | Filters the feed to one label. |
| `GET /api/candidates/<mint>` | Returns one candidate and its saved snapshots. |
| `GET /api/cohorts` | Existing public early-buyer co-buy research report. |
| `GET /api/ingest-wallet?address=...` | Existing bounded public Helius wallet-history import. |

## Candidate rules in this first version

A real candidate will eventually need to pass:

- Primary market-cap lane: roughly $100k–$750k.
- At least $25k liquidity; $50k+ is stronger.
- At least five minutes of trading history.
- Favorable recent buy/sell pressure and volume relative to liquidity.
- A small live sell quote below the configured price-impact threshold.
- No configured token, creator, or linked-holder risk flags.

Live records whose safety check is still pending can be shown as `Watch`, but
cannot become a `Candidate`. Sample fixtures are the only temporary exception,
and the dashboard labels them clearly.

## Current discovery rules

The DEX Screener refresh considers only a small public Solana shortlist, then
keeps pairs that are at least five minutes old with roughly $75k–$3m market cap,
$25k liquidity, $1k five-minute volume, and at least five recent swaps. These
are discovery filters, not quality or safety proof.

## Next build step

Add Solana Tracker token-risk checks, then public Helius wallet evidence, to the
small group that survives the sell-route check.

## Safety rules

- Public-chain research only.
- No wallet connection, transaction signing, auto-buying, or copy-trading.
- Treat linked-wallet findings as unverified evidence, never proof of identity
  or intent.
- Never put API keys, seed phrases, private keys, or passwords in Git.
