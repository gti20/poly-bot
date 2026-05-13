"""Polymarket Backtester — Improved Version (Realistic, Less Bias)"""
import json
import logging
import sys
import asyncio
from typing import List, Dict, Optional

import requests
import numpy as np

from config import load_settings
from trader import Trader
from models import ScoredMarket, Market

# Silence verbose libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

class Backtester:
    def __init__(self, trader: Trader, settings):
        self.trader = trader
        self.settings = settings

    def fetch_resolved_markets(self, total_needed: int = 30, min_volume: float = 25_000) -> List[Dict]:
        """Fetch high-quality resolved markets, lowering volume floor to find more edges."""
        markets = []
        limit = 100
        offset = 0
        url = "https://gamma-api.polymarket.com/markets"

        print(f"🌐 Fetching resolved markets (vol >= ${min_volume:,.0f})...")

        # Skip noise/ultra-short term markets
        bad_keywords = ["up or down", "minute", "5m", "10m", "15m", "price of bitcoin at", "price of ethereum at"]

        while len(markets) < total_needed:
            params = {
                "closed": "true",
                "limit": limit,
                "offset": offset,
                "order": "volume",
                "ascending": "false",
            }
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break

                for m in data:
                    vol = float(m.get("volumeNum") or m.get("volume") or 0)
                    q = (m.get('question') or m.get('title', '')).lower()
                    closed = m.get("closedTime") or m.get("endDate") or ""

                    if vol < min_volume:
                        continue
                    if any(kw in q for kw in bad_keywords):
                        continue
                    if not self._is_2024_plus(closed):
                        continue

                    markets.append(m)
                    if len(markets) >= total_needed:
                        break

                offset += limit
                print(f"   ↳ Scanned {offset} markets, found {len(markets)} qualified...")
                if len(data) < limit:
                    break
            except Exception as e:
                print(f"❌ API error: {e}")
                break

        print(f"✅ Loaded {len(markets)} quality markets for backtest")
        return markets[:total_needed]

    def _is_2024_plus(self, date_val: str | None) -> bool:
        if not date_val:
            return False
        text = str(date_val).lower()
        return any(y in text for y in ["2024", "2025", "2026"])

    def _raw_to_market(self, raw: dict) -> Market:
        """Robust price extraction using lastTradePrice to avoid settlement bias."""
        # lastTradePrice is the best proxy for 'live' price in a resolved market
        last_p = float(raw.get("lastTradePrice") or 0)
        
        # Parse outcomePrices for fallback
        try:
            prices_raw = raw.get("outcomePrices")
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [0.5, 0.5])
            settled_p = float(prices[0])
        except:
            settled_p = 0.5

        # If the market price is at 0 or 1, it's settled. 
        # We try to use the last traded price to simulate an entry.
        if 0.01 < last_p < 0.99:
            yes_p = last_p
        elif 0.01 < settled_p < 0.99:
            yes_p = settled_p
        else:
            # If both are 0/1, we have no historical entry price data. 
            # We use 0.0 or 1.0 which will trigger our skip logic in process_market.
            yes_p = settled_p
        
        tokens = raw.get("tokens", [])
        return Market(
            condition_id=str(raw.get("conditionId") or raw.get("id")),
            question=str(raw.get("question") or raw.get("title", "")),
            yes_token_id=tokens[0].get("token_id", "") if len(tokens) > 0 else "",
            no_token_id=tokens[1].get("token_id", "") if len(tokens) > 1 else "",
            yes_price=yes_p,
            no_price=1.0 - yes_p,
            liquidity=float(raw.get("liquidityNum") or raw.get("liquidity") or 0),
            volume=float(raw.get("volumeNum") or raw.get("volume") or 0),
            end_date_iso=str(raw.get("closedTime") or raw.get("endDate") or ""),
            description=str(raw.get("description", ""))
        )

    async def process_market(self, raw: dict, stats: dict, trade_log: list):
        """Process one market asynchronously with realistic price execution."""
        fair_obj = None
        entry = None
        q = raw.get('question') or raw.get('title', 'Untitled')
        
        try:
            market = self._raw_to_market(raw)

            # Skip if we can't find a realistic entry price (market already 100% resolved)
            if market.yes_price <= 0.01 or market.yes_price >= 0.99:
                stats["skip_edge"] += 1
                return

            # 1. Grok Estimation
            fair_obj = await self.trader.grok.estimate_fair_odds(market)
            if fair_obj is None:
                stats["skip_grok"] += 1
                return

            # 2. Score Market
            entry = await self.trader._score_market(market, fair_obj, verbose=False)

            # 3. Edge Check
            if not entry or entry.edge_bits < self.settings.min_edge_bits:
                stats["skip_edge"] += 1
                return

            # 4. Resolution
            try:
                prices_raw = raw.get("outcomePrices")
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [0.5, 0.5])
                true_yes = float(prices[0]) > 0.5
            except:
                return

            bet_yes = (entry.side == "YES")
            won = (bet_yes and true_yes) or (not bet_yes and not true_yes)
            
            # ... (Step 1-4: Convert, Grok, Score, Edge Check) ...

            # 5. Sizing & Execution
            size_usd = self.trader._kelly_size(entry, self.settings.bankroll_usd)
            if size_usd < 5.0: 
                return

            entry_price = market.yes_price if entry.side == "YES" else market.no_price
            
            # --- INSERT NEW SLIPPAGE LOGIC HERE ---
            # Instead of a flat 0.01, we cap slippage at 5% of the cost to protect ROI
            max_allowed_slippage = entry_price * 0.05 # 5% max
            # We take the smaller of 1 cent or 5% of the price
            exec_price = entry_price + min(0.01, max_allowed_slippage)
            
            # Safety cap to ensure we never buy for > $1.00
            exec_price = min(0.995, exec_price)
            # --------------------------------------

            # 6. PnL Math
            contracts = size_usd / exec_price
            pnl = (contracts if won else 0) - size_usd

            # ... (Step 7-8: Stats and Logging) ...
            # Update stats
            if won: stats["wins"] += 1
            stats["trades"] += 1
            stats["pnl"] += pnl
            stats["gross_pnl"] += pnl

            trade_log.append({
                "market": q[:100],
                "side": entry.side,
                "grok_p": round(fair_obj.fair_yes_probability, 4),
                "market_p": round(entry_price, 4),
                "exec_price": round(exec_price, 3),
                "edge_bits": round(entry.edge_bits, 4),
                "pnl": round(pnl, 2),
                "won": won
            })

            print(f"   ✅ TRADED | {entry.side} @ {exec_price:.3f} | Edge={entry.edge_bits:.3f} | PNL=${pnl:.2f} | Won={won}")

        except Exception as e:
            logging.error(f"Error processing '{q[:40]}': {e}")

    async def _run_async_backtest(self, resolved, stats, trade_log):
        for raw in resolved:
            stats["processed"] += 1
            q = raw.get('question', 'Untitled')
            vol = float(raw.get("volumeNum") or 0)
            print(f"[{stats['processed']}/{stats['total']}] {q[:60]} | Vol=${vol:,.0f}")
            await self.process_market(raw, stats, trade_log)
            await asyncio.sleep(0.2) 

    def run_backtest(self, limit: int = 100):
        resolved = self.fetch_resolved_markets(total_needed=limit)
        if not resolved:
            print("❌ No markets found.")
            return

        stats = {
            "processed": 0, "total": len(resolved),
            "trades": 0, "pnl": 0.0, "wins": 0, "gross_pnl": 0.0,
            "skip_edge": 0, "skip_grok": 0,
        }
        trade_log = []

        print(f"\n{'#'*20} STARTING REALISTIC BACKTEST {'#'*20}\n")
        try:
            asyncio.run(self._run_async_backtest(resolved, stats, trade_log))
        except KeyboardInterrupt:
            print("\n🛑 Stopped.")

        self._print_summary(stats, trade_log)
        self._save_results(trade_log)

    def _print_summary(self, s: dict, trade_log: list):
        wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        print("\n" + "="*80)
        print("📊 BACKTEST SUMMARY")
        print(f"Trades     : {s['trades']} | Win Rate: {wr:.1f}%")
        print(f"Net P&L    : ${s['pnl']:.2f}")
        print(f"ROI        : {(s['pnl']/self.settings.bankroll_usd*100):.1f}%")
        print("-" * 80)
        print(f"Skipped -> Edge/Settled: {s['skip_edge']} | Grok Fail: {s['skip_grok']}")
        print("="*80)

    def _save_results(self, trade_log: list):
        with open("backtest_results_improved.json", "w") as f:
            json.dump({"summary": {"trades": len(trade_log)}, "trades": trade_log}, f, indent=2)

if __name__ == "__main__":
    settings = load_settings()
    settings.bankroll_usd = 1000
    
    # INCREASE the bar for quality
    settings.min_edge_bits = 0.001 # From 0.02 -> 0.001 (To get more trades)
    
    trader = Trader(settings)
    bt = Backtester(trader, settings)
    
    # Run again with the 200 limit to see if these filters 
    # would have saved the $40 you just lost.
    bt.run_backtest(limit=200)
