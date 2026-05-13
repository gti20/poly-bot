"""Core trading strategy — POWERHOUSE EDITION (Real-Time Prices + Safe Positions)"""
from __future__ import annotations

import logging
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, List

from config import Settings
from grok_client import GrokClient
from external_signals import ExternalSignals
from models import FairOdds, Market, PositionValue, RunResult, ScoredMarket
from polymarket_client import PolymarketClient
from info_theory import kl_divergence, max_entropy_fusion, entropy

logger = logging.getLogger(__name__)

class Trader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.polymarket = PolymarketClient(settings)
        self.grok = GrokClient(settings)
        self.external = ExternalSignals(settings)
        self.peak_equity: float = settings.bankroll_usd
        self.performance_log_path = Path("performance_log.json")
        self.entropy_histories: dict[str, list[float]] = {}
        # One consistent client for run_bot.py compatibility
        self.client = self.polymarket 

    def calculate_dry_run_pnl(self, current_positions: List[PositionValue], markets: List[Market]) -> float:
        """Calculates simulated Mark-to-Market value for held positions."""
        total_val = 0.0
        for pos in current_positions:
            market_update = next((m for m in markets if m.question == pos.question), None)
            if market_update:
                current_price = market_update.yes_price if pos.side == "YES" else market_update.no_price
                pos.current_bid = current_price
                pos.mark_value = pos.size * current_price
                total_val += pos.mark_value
        return total_val

    async def run_once(
        self,
        top_n: int = 10,
        stream_progress: bool = False,
        progress_callback: Callable[[str], None] | None = None,
        verbose: bool = True,
    ) -> RunResult:
        s = self.settings
        logs: list[str] = []
        attempted = placed = 0
        position_values = []
        total_pos_val = 0.0

        def _log(msg: str) -> None:
            logs.append(msg)
            if stream_progress or verbose:
                print(msg)

        _log(f"Bankroll: ${s.bankroll_usd:.2f} | Dry Run: {getattr(s, 'dry_run', True)}")

        # 1. FETCH MARKETS
        try:
            # Explicitly calling this synchronously to avoid "list can't be awaited" errors
            raw_markets = self.polymarket.get_active_markets(limit=s.market_pool_size)
        except Exception as e:
            _log(f"❌ Failed to fetch markets: {e}")
            raw_markets = []

        _log(f"📡 API Check: Received {len(raw_markets)} raw markets.")

        candidates = []
        for m in raw_markets:
            min_liq = getattr(s, 'min_liquidity_usd', 1000) 
            if m.liquidity < min_liq:
                continue

            try:
                # Real-time midpoint check
                m.yes_price = self.polymarket.get_order_book_midpoint(m.yes_token_id) or 0.50
                m.no_price = self.polymarket.get_order_book_midpoint(m.no_token_id) or 0.50
            except:
                m.yes_price, m.no_price = 0.50, 0.50

            if m.yes_price < 0.05 or m.yes_price > 0.95:
                continue
            candidates.append(m)

        scored: list[ScoredMarket] = []
        num_to_score = min(len(candidates), s.max_markets)
        
        for i, market in enumerate(candidates[:num_to_score]):
            _log(f"Scoring [{i+1}/{num_to_score}] {market.question[:60]}...")
            fair = await self.grok.estimate_fair_odds(market)
            
            if not fair:
                _log("    ↳ Skipped — Grok failed")
                continue

            max_div = getattr(s, 'max_divergence', 0.25)
            if abs(fair.fair_yes_probability - market.yes_price) > max_div:
                _log(f"    ↳ Skipped — Divergence too high")
                continue

            entry = await self._score_market(market, fair, verbose=verbose)
            if entry:
                scored.append(entry)
                _log(f"    ⭐ SCORED! Edge: {entry.edge_bits:.4f} bits")

        # Sorting markets by highest edge bits (Info Theory ranking)
        scored = self._rank(scored)

        # 2. EXECUTION LOGIC
        for entry in scored[:top_n]:
            if placed >= getattr(s, 'max_positions_per_run', 5):
                break

            size = self._kelly_size(entry, s.bankroll_usd)
            if size < 10.0: continue

            token_id = entry.market.yes_token_id if entry.side == "YES" else entry.market.no_token_id
            entry_price = entry.market.yes_price if entry.side == "YES" else entry.market.no_price
            exec_price = min(0.995, entry_price + 0.01)

            _log(f"ORDER {entry.side} | {entry.market.question[:40]} | Mkt={entry_price:.3f} | Size=${size:.2f}")

            attempted += 1
            if not getattr(s, 'dry_run', True):
                resp = self.polymarket.place_limit_order(token_id=token_id, price=exec_price, size=size/exec_price, side="BUY")
                if resp: placed += 1

        # 3. FINAL P&L TRACKING
        try:
            open_positions = self.polymarket.get_open_positions()
            position_values, _ = self._mark_positions(open_positions)
        except:
            pass

        if getattr(s, 'dry_run', True) and position_values:
            total_pos_val = self.calculate_dry_run_pnl(position_values, candidates)

        return RunResult(
            scored=scored,
            attempted_orders=attempted,
            placed_orders=placed,
            attempted_exits=0,
            placed_exits=0,
            effective_bankroll_usd=s.bankroll_usd,
            bankroll_source="Settings",
            total_position_value=total_pos_val,
            position_values=position_values,
            evaluation_logs=logs,
        )

    async def _score_market(self, market: Market, grok_fair: FairOdds, verbose: bool = False) -> ScoredMarket | None:
        s = self.settings
        grok_p = grok_fair.fair_yes_probability
        news_p = self.grok.get_news_sentiment(market) if getattr(s, 'use_news_sentiment', True) else 0.5
        sharp_p = await self.external.get_sharp_consensus(market) if getattr(s, 'use_sharp_signal', True) else 0.52
        
        # Max Entropy Fusion of signals
        fused_p = max_entropy_fusion([grok_p, news_p, sharp_p])
        market_q = [market.yes_price, 1 - market.yes_price]
        your_p = [fused_p, 1 - fused_p]
        edge_bits = kl_divergence(your_p, market_q)

        if verbose:
            print(f"MARKET: {market.question[:30]} | BITS: {edge_bits:.6f} | REQ: {s.min_edge_bits}")

        if getattr(s, 'use_info_theory', True) and edge_bits < getattr(s, 'min_edge_bits', 0.015):
            return None

        yes_edge = fused_p - market.yes_price
        side = "YES" if yes_edge >= 0 else "NO"
        
        return ScoredMarket(
            market=market, fair_odds=grok_fair, side=side,
            edge=abs(yes_edge), divergence=abs(yes_edge),
            edge_bits=edge_bits, entropy=entropy(market_q), fused_prob=fused_p
        )

    def _kelly_size(self, entry: ScoredMarket, bankroll: float) -> float:
        s = self.settings
        p, c = entry.fused_prob, (entry.market.yes_price if entry.side == "YES" else entry.market.no_price)
        if c <= 0.01 or c >= 0.99: return 0.0
        try:
            # fractional Kelly calculation
            kelly_f = (p - c) / max(0.001, (1 - c)) if entry.side == "YES" else ((1 - p) - (1 - c)) / max(0.001, c)
            kelly_f = max(0.0, min(kelly_f, 0.4)) * getattr(s, 'kelly_multiplier', 0.15)
            return min(bankroll * kelly_f, entry.market.liquidity * 0.05)
        except: return 10.0

    def _rank(self, scored: list[ScoredMarket]) -> list[ScoredMarket]:
        return sorted(scored, key=lambda e: (-e.edge_bits, -e.market.liquidity))

    def _mark_positions(self, positions: list[dict]) -> tuple[list[PositionValue], float]:
        # Placeholder for real position marking
        return [], 0.0

    def _update_performance_tracking(self, result: RunResult) -> None:
        logger.info(f"✅ Run complete | Placed {result.placed_orders} orders")
