"""Polymarket Backtester — runs your exact strategy on historical resolved markets"""
import json
import logging
import sys
import traceback
from typing import List, Dict, Any

import requests

from config import load_settings
from trader import Trader

# Force output immediately
print("🚀 Starting Backtester...")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

logger = logging.getLogger(__name__)


class Backtester:
    def __init__(self, trader, settings):
        self.trader = trader
        self.settings = settings
        self.results = []

    def fetch_resolved_markets(self, limit: int = 200) -> List[Dict]:
        """Fetch resolved markets from Polymarket API"""
        try:
            print(f"🌐 Fetching {limit} most recent resolved markets...")
            url = "https://gamma-api.polymarket.com/markets"
            params = {
                "closed": "true",
                "limit": limit,
                "order": "closed_time",
                "ascending": "false"   # Most recent first
            }

            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            markets = response.json()

            print(f"✅ Fetched {len(markets)} resolved markets")
            return markets

        except Exception as e:
            print(f"❌ Failed to fetch markets: {e}")
            logger.error(traceback.format_exc())
            return []

    def run_backtest(self, limit: int = 200) -> None:
        resolved = self.fetch_resolved_markets(limit=limit)
        if not resolved:
            print("⚠️ No markets fetched — stopping.")
            return

        total_trades = 0
        total_pnl = 0.0
        skipped_vol = skipped_short = skipped_cat = skipped_edge = skipped_tennis = 0

        for i, raw_market in enumerate(resolved[:limit]):
            q = raw_market.get('question', raw_market.get('title', 'Untitled'))[:120]
            volume = float(raw_market.get("volumeNum", raw_market.get("volume", 0) or 0))

            print(f"[{i+1}/{limit}] {q} | Vol=${volume:,.0f}")

            # === VOLUME FILTER ===
            if volume < 50000:
                skipped_vol += 1
                print("   ↳ Skipped — volume too low (<$50k)")
                continue

            q_lower = q.lower()

            # === SKIP SHORT-TERM NOISE ===
            short_terms = ["minute", "am et", "pm et", "up or down", "10:", "15:", "20:", "25:", "30:"]
            if any(x in q_lower for x in short_terms):
                skipped_short += 1
                print("   ↳ Skipped — too short-term (noise)")
                continue

            # === ALLOWED CATEGORIES ===
            allowed_keywords = [
                "trump", "election", "president", "congress", "2028", "2026", "midterm",
                "bitcoin", "btc", "ethereum", "eth", "solana", "arsenal", "atlético",
                "champions league", "premier league", "nba", "world cup", "uefa",
                "oil", "fed", "inflation", "rate", "interest"
            ]

            if not any(word in q_lower for word in allowed_keywords):
                if any(t in q_lower for t in ["vs.", "set ", "match o/u", "tennis", "°c", "temperature", "will the highest"]):
                    skipped_tennis += 1
                    print("   ↳ Skipped — tennis/weather/micro")
                    continue
                else:
                    skipped_cat += 1
                    print("   ↳ Skipped — low-signal category")
                    continue

            # === TRADING LOGIC ===
            try:
                market = self._raw_to_market(raw_market)
                fair = self.trader.grok.estimate_fair_odds(market)
                if fair is None:
                    print("   ↳ Skipped — Grok failed")
                    continue

                entry = self.trader._score_market(market, fair)
                if entry is None or getattr(entry, 'edge_bits', 0) < 0.18:
                    skipped_edge += 1
                    print("   ↳ Skipped — edge too small")
                    continue

                # Resolution logic
                try:
                    prices_raw = raw_market.get("outcomePrices", "[0.5, 0.5]")
                    if isinstance(prices_raw, str):
                        prices = json.loads(prices_raw)
                    else:
                        prices = prices_raw
                    true_outcome = 1.0 if float(prices[0]) > 0.9 else 0.0
                except:
                    true_outcome = 0.0

                bet_on_yes = entry.side == "YES"
                won = (bet_on_yes and true_outcome == 1.0) or (not bet_on_yes and true_outcome == 0.0)

                price = market.yes_price if bet_on_yes else market.no_price
                if price <= 0.01 or price >= 0.99:
                    price = 0.50 if entry.fused_prob > 0.5 else 0.48

                size = self.trader._kelly_size(entry, self.settings.bankroll_usd)
                if size < 8.0:
                    print("   ↳ Skipped — size too small")
                    continue

                contracts = size / price
                pnl = (contracts * (1.0 if won else 0.0)) - size

                total_trades += 1
                total_pnl += pnl
                self.results.append({"won": won, "pnl": pnl})

                print(f"   ✅ TRADED | {entry.side} | Fused={entry.fused_prob:.3f} | "
                      f"Bits={getattr(entry,'edge_bits',0):.3f} | PNL=${pnl:.2f} | Won={won}")

            except Exception as e:
                print(f"   ❌ Error processing market: {e}")

        # === SUMMARY ===
        win_rate = sum(1 for r in self.results if r.get("won")) / len(self.results) if self.results else 0
        print("\n" + "="*90)
        print("🎯 BACKTEST COMPLETE")
        print(f"Trades taken          : {total_trades}")
        print(f"Win rate              : {win_rate:.1%}")
        print(f"Total P&L             : ${total_pnl:.2f}")
        print(f"Avg P&L/trade         : ${total_pnl / total_trades if total_trades else 0:.2f}")
        print(f"Skipped low vol       : {skipped_vol}")
        print(f"Skipped short-term    : {skipped_short}")
        print(f"Skipped low category  : {skipped_cat}")
        print(f"Skipped tennis/weather: {skipped_tennis}")
        print(f"Skipped low edge      : {skipped_edge}")
        print("="*90)

        with open("backtest_results.json", "w") as f:
            json.dump(self.results, f, indent=2)

    def _raw_to_market(self, raw: dict):
        class DummyMarket:
            def __init__(self, d):
                self.__dict__.update(d)
                try:
                    prices_raw = d.get("outcomePrices", "[0.5, 0.5]")
                    if isinstance(prices_raw, str):
                        prices = json.loads(prices_raw)
                    else:
                        prices = prices_raw
                    self.yes_price = float(prices[0])
                    self.no_price = 1.0 - self.yes_price
                except:
                    self.yes_price = 0.5
                    self.no_price = 0.5

                self.liquidity = float(d.get("liquidityNum", d.get("liquidity", 1000)))
                self.volume = float(d.get("volumeNum", d.get("volume", 0)))
                self.condition_id = d.get("conditionId") or d.get("id") or ""
                self.question = d.get("question") or d.get("title") or "Untitled"
                self.description = d.get("description", "")

        return DummyMarket(raw)


if __name__ == "__main__":
    try:
        settings = load_settings()
        trader = Trader(settings)          # Assuming your Trader class accepts settings
        bt = Backtester(trader, settings)
        bt.run_backtest(limit=50)          # Start with 50 for testing
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        traceback.print_exc()
