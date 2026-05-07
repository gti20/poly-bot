"""Polymarket Backtester — Clean & Working Version"""
import json
import logging
import sys
from typing import List, Dict

import requests

from config import load_settings
from trader import Trader

# Silence noisy logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)


class Backtester:
    def __init__(self, trader: Trader, settings):
        self.trader = trader
        self.settings = settings
        self.results = []

    def fetch_resolved_markets(self, total_needed: int = 150) -> List[Dict]:
        """Fetch resolved markets with pagination"""
        markets = []
        limit = 50
        offset = 0
        url = "https://gamma-api.polymarket.com/markets"

        print(f"🌐 Fetching up to {total_needed} resolved markets...")

        while len(markets) < total_needed:
            params = {
                "closed": "true",
                "limit": limit,
                "offset": offset,
                "order": "closedTime",
                "ascending": "false"
            }
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                if not data:
                    break
                markets.extend(data)
                offset += limit
                print(f"   ↳ Progress: {len(markets)}/{total_needed}")
                if len(data) < limit:
                    break
            except Exception as e:
                print(f"❌ API error: {e}")
                break

        return markets[:total_needed]

    def is_recent(self, date_val: str | None) -> bool:
        """Skip pre-2024 markets"""
        if not date_val:
            return False
        text = str(date_val)
        if any(y in text for y in ["2020", "2021", "2022", "2023"]):
            return False
        return True

    def run_backtest(self, limit: int = 150) -> None:
        resolved = self.fetch_resolved_markets(total_needed=limit)
        if not resolved:
            print("❌ No markets fetched.")
            return

        stats = {"trades": 0, "pnl": 0.0, "wins": 0,
                 "skip_vol": 0, "skip_old": 0, "skip_edge": 0, "skip_grok": 0}

        print(f"\n{'#'*20} STARTING EVALUATION {'#'*20}\n")

        for i, raw in enumerate(resolved):
            q = raw.get('question') or raw.get('title', 'Untitled')
            volume = float(raw.get("volumeNum") or raw.get("volume") or 0)
            closed_time = raw.get("closedTime") or raw.get("endDate")

            print(f"[{i+1}/{limit}] {q[:70]} | Vol=${volume:,.0f}")

            if not self.is_recent(closed_time):
                stats["skip_old"] += 1
                continue

            if volume < 40_000:
                stats["skip_vol"] += 1
                continue

            try:
                market = self._raw_to_market(raw)

                fair_obj = self.trader.grok.estimate_fair_odds(market)
                if fair_obj is None:
                    stats["skip_grok"] += 1
                    print(f"   ⚠️ Grok failed on {q[:50]}...")
                    continue

                # Debug probabilities
                grok_p = getattr(fair_obj, 'fair_yes_probability', 0.5)
                print(f"   🔍 Grok: {grok_p:.3f} | Market YES: {market.yes_price:.3f}")

                # Score market (pass FairOdds object)
                entry = self.trader._score_market(market, fair_obj, verbose=False)
                if entry is None:
                    stats["skip_edge"] += 1
                    continue

                # Resolution
                try:
                    prices = json.loads(raw.get("outcomePrices", "[0.5,0.5]"))
                    true_yes = float(prices[0]) > 0.9
                except:
                    true_yes = False

                bet_yes = entry.side == "YES"
                won = (bet_yes and true_yes) or (not bet_yes and not true_yes)
                if won:
                    stats["wins"] += 1

                size = self.trader._kelly_size(entry, self.settings.bankroll_usd)
                if size < 8.0:
                    continue

                price = max(0.01, min(0.99, market.yes_price if bet_yes else market.no_price))
                contracts = size / price
                pnl = (contracts if won else 0) - size
                pnl *= 0.98  # fees

                stats["trades"] += 1
                stats["pnl"] += pnl

                print(f"   ✅ TRADED | {entry.side} | Fused={getattr(entry,'fused_prob', grok_p):.3f} | "
                      f"PNL=${pnl:.2f} | Won={won}")

            except Exception as e:
                logging.error(f"Error on '{q[:40]}': {e}")

        self._print_summary(stats)

    def _print_summary(self, s: dict):
        wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        print("\n" + "="*70)
        print("📊 BACKTEST SUMMARY (2024-2026)")
        print(f"Processed : {s['trades']} Trades | Win Rate: {wr:.1f}%")
        print(f"Total P&L : ${s['pnl']:.2f}")
        print("-" * 70)
        print(f"Skipped → Old: {s['skip_old']} | Low Vol: {s['skip_vol']} | Grok Fail: {s['skip_grok']} | Low Edge: {s['skip_edge']}")
        print("="*70)

    def _raw_to_market(self, raw: dict):
        class DummyMarket:
            def __init__(self, d):
                self.__dict__.update(d)
                try:
                    prices = json.loads(d.get("outcomePrices", "[0.5,0.5]"))
                    self.yes_price = float(prices[0])
                    self.no_price = 1.0 - self.yes_price
                except:
                    self.yes_price = self.no_price = 0.5

                self.liquidity = float(d.get("liquidityNum", d.get("liquidity", 0)))
                self.volume = float(d.get("volumeNum", d.get("volume", 0)))
                self.condition_id = d.get("conditionId") or d.get("id")
                self.question = d.get("question") or d.get("title", "")
                self.description = d.get("description", "")

        return DummyMarket(raw)


if __name__ == "__main__":
    settings = load_settings()
    settings.bankroll_usd = getattr(settings, 'bankroll_usd', 1000)
    settings.dry_run = True

    trader = Trader(settings)
    bt = Backtester(trader, settings)
    bt.run_backtest(limit=50)
