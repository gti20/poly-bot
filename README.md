# Polymarket Grok Trader

Automated prediction market trading bot for **Polymarket** that uses the **Grok API** to estimate fair probabilities, finds mispriced contracts, and places sized orders via the Polymarket CLOB.

Equivalent of [neelsomani/grok-prediction](https://github.com/neelsomani/grok-prediction) but for Polymarket instead of Kalshi.

---

## How it works

For each active Polymarket market:

1. Pulls YES/NO prices and liquidity from the Gamma + CLOB APIs.
2. Asks Grok for a `fair_yes_probability`.
3. Computes edge on both sides:
   - `yes_edge = fair_yes_probability − yes_price`
   - `no_edge = (1 − fair_yes_probability) − no_price`
4. Takes the side with higher positive edge (if above `TRADER_MIN_EDGE`).
5. Sizes the position with **fractional Kelly**:
   - `f* = (p − c) / (1 − c)`
   - Multiplied by `TRADER_KELLY_MULTIPLIER` (default 0.25 = quarter-Kelly)
   - Capped at `TRADER_KELLY_FRACTION_CAP` and `TRADER_MAX_BET_TO_LIQUIDITY_RATIO`
6. Ranks by divergence band → liquidity → volume → raw divergence.
7. Places GTC limit orders on the CLOB for the top N opportunities.

**Exit management:** re-checks open positions each run. If the current bid minus average cost drops below `TRADER_EXIT_EDGE_THRESHOLD`, a sell order is submitted.

**Dry-run mode is on by default** — no live orders until you set `TRADER_DRY_RUN=false`.

---

## Setup

### 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | Your wallet private key (hex) |
| `POLYMARKET_FUNDER` | Wallet address holding your USDC on Polygon |
| `GROK_API_KEY` | Your xAI / Grok API key |
| `GROK_MODEL` | Grok model ID (run `--list-grok-models`) |

Optional risk controls — see `.env.example` for full list.

### 3. Wallet setup (one-time)

Polymarket uses the **Polygon** network with USDC as collateral. Before trading:

- Fund your wallet with USDC on Polygon.
- If using a MetaMask / EOA wallet, set token allowances once:

```python
# Uses py-clob-client helpers — see py-clob-client docs for set_allowances.py
```

- Email/Magic wallets set allowances automatically.

---

## Usage

### CLI

```bash
# Dry run (safe default) — score markets, show what would be ordered
PYTHONPATH=src python -m polymarket_grok_trader.cli --top 10

# With Grok rationale printed
PYTHONPATH=src python -m polymarket_grok_trader.cli --top 10 --verbose

# Check auth
PYTHONPATH=src python -m polymarket_grok_trader.cli --auth-check

# Portfolio status
PYTHONPATH=src python -m polymarket_grok_trader.cli --status

# List available Grok models
PYTHONPATH=src python -m polymarket_grok_trader.cli --list-grok-models

# Close all positions (irreversible in live mode)
PYTHONPATH=src python -m polymarket_grok_trader.cli --close-all
```

Or after installing as a package:

```bash
pip install -e .
polymarket-grok-trader --top 10
```

### Streamlit dashboard

```bash
streamlit run streamlit_app.py
```

One-click buttons for: Run Strategy, Refresh Status, Close All, Auth Check, List Grok Models.

---

## Live trading

Set `TRADER_DRY_RUN=false` in `.env`. Start with a small `TRADER_BANKROLL_USD` and tight caps.

Key differences from Kalshi:

- **Authentication**: Polygon wallet + private key (EIP-712 signing), not a Kalshi API key + PEM.
- **Settlement**: on-chain via Polygon smart contracts — trades are non-custodial.
- **Token model**: each outcome is a conditional ERC-1155 token; prices are in USDC (0–1).
- **No US residents**: Polymarket geo-restricts US IP addresses.

---

## Project structure

```
polymarket-grok-trader/
├── src/polymarket_grok_trader/
│   ├── __init__.py
│   ├── config.py            # Settings from env
│   ├── models.py            # Shared dataclasses
│   ├── polymarket_client.py # CLOB + Gamma API wrapper
│   ├── grok_client.py       # Grok fair-odds estimator
│   ├── trader.py            # Core strategy logic
│   └── cli.py               # CLI entrypoint
├── streamlit_app.py         # Dashboard UI
├── .env.example
├── requirements.txt
└── pyproject.toml
```

---

## Disclaimer

Grok estimates are model outputs and can be wrong. This is not financial advice. Prediction market trading involves real financial risk. Use dry-run mode and small position sizes until you understand the system's behaviour.
