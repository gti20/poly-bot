"""Core trading strategy — Polymarket Grok Trader (Quant Upgraded with Shannon/Thorp + Multi-Signal)"""
from __future__ import annotations

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import Settings
from grok_client import GrokClient
from models import FairOdds, Market, PositionValue, RunResult, ScoredMarket
from polymarket_client import PolymarketClient
from info_theory import kl_divergence, max_entropy_fusion, entropy, insider_alert

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.polymarket = PolymarketClient(settings)
        self.grok = GrokClient(settings)
        self.peak_equity: float = settings.bankroll_usd
        self.performance_log_path = Path(getattr(settings, "performance_log_file", "performance_log.json"))
        self.entropy_histories: dict[str, list[float]] = {}

    # ── Main Entry Point ───────────────────────────────────────────────────

    def run_once(
        self,
        top_n: int = 10,
        stream_progress: bool = False,
        progress_callback: Callable[[str], None] | None = None,
        verbose: bool = False,
    ) -> RunResult:
        """Run one full strategy cycle."""
        s = self.settings
        logs: list[str] = []

        def _log(msg: str) -> None:
            logs.append(msg)
            if stream_progress:
                print(msg)
            if progress_callback:
                progress_callback(msg)

        # 1. Bankroll
        bankroll_source = "config"
        effective_bankroll = s.bankroll_usd

        if getattr(s, "use_live_balance", False):
            live = self.polymarket.get_usdc_balance()
            if live is None:
                _log("ERROR: live balance fetch failed — aborting")
                return RunResult(scored=[], attempted_orders=0, placed_orders=0,
                                 attempted_exits=0, placed_exits=0,
                                 effective_bankroll_usd=0.0, bankroll_source="failed",
                                 total_position_value=0.0, position_values=[], evaluation_logs=logs)
            effective_bankroll = min(live, getattr(s, "bankroll_cap_usd", 10000))
            bankroll_source = "live"
            _log(f"Live USDC: ${live:.2f} → capped at ${effective_bankroll:.2f}")

        _log(f"Bankroll: ${effective_bankroll:.2f} (source={bankroll_source})")

        # 2. Existing positions/orders
        open_orders = self.polymarket.get_open_orders()
        open_positions = self.polymarket.get_open_positions()
        existing_condition_ids = {
            pos.get("conditionId") or pos.get("condition_id") or "" 
            for pos in open_positions + open_orders if pos
        }

        _log(f"Existing positions/orders: {len(existing_condition_ids)}")

        # 3. Fetch & enrich markets
        raw_markets = self.polymarket.get_active_markets(limit=getattr(s, "market_pool_size", 200))
        _log(f"Fetched {len(raw_markets)} markets")

        enriched_markets = []
        for market in raw_markets:
            mid_yes = self.polymarket.get_order_book_midpoint(market.yes_token_id)
            mid_no = self.polymarket.get_order_book_midpoint(market.no_token_id)

            if mid_yes is not None:
                market.yes_price = mid_yes
            if mid_no is not None:
                market.no_price = mid_no

            # Relaxed thin-book filter
            if (abs(market.yes_price - 0.5) < 0.01 and abs(market.no_price - 0.5) < 0.01 
                and market.liquidity < 500):
                continue

            enriched_markets.append(market)

            if verbose:
                _log(f"    ↳ {market.question[:65]:<65} YES@{market.yes_price:.4f} NO@{market.no_price:.4f}")

        raw_markets = enriched_markets
        _log(f"Kept {len(raw_markets)} markets")

        candidate_markets = [
            m for m in raw_markets 
            if m.condition_id not in existing_condition_ids and m.liquidity > 0
        ]
        _log(f"Candidates: {len(candidate_markets)}")

        # 4. Score markets
        scored: list[ScoredMarket] = []
        for i, market in enumerate(candidate_markets[: getattr(s, "max_markets", 50)]):
            _log(f"  [{i+1}/{min(getattr(s, 'max_markets', 50), len(candidate_markets))}] Scoring: {market.question[:70]}")
            
            fair = self.grok.estimate_fair_odds(market)
            if fair is None:
                _log("    ↳ skip — Grok failed")
                continue

            entry = self._score_market(market, fair, verbose=verbose)
            if entry is None:
                _log("    ↳ skip — edge too small")
                continue

            _log(f"    ↳ {entry.side} edge={entry.edge:.4f} bits={getattr(entry, 'edge_bits', 0):.3f} liq=${market.liquidity:.0f}")
            scored.append(entry)

        # Insider alerts
        for entry in scored:
            cid = entry.market.condition_id
            if insider_alert(self.entropy_histories.get(cid, []), getattr(s, "insider_sigma", 3.0)):
                _log(f"🚨 INSIDER ALERT on {entry.market.question[:60]} — entropy collapse!")

        scored = self._rank(scored)
        _log(f"Scored and ranked {len(scored)} opportunities")

        # 5. Place orders
        attempted = placed = 0
        for entry in scored[:top_n]:
            if placed >= getattr(s, "max_positions_per_run", 5):
                break

            size = self._kelly_size(entry, effective_bankroll)
            if size < 1.0:
                continue

            token_id = entry.market.yes_token_id if entry.side == "YES" else entry.market.no_token_id
            price = entry.market.yes_price if entry.side == "YES" else entry.market.no_price
            contracts = size / price

            _log(f"  ORDER {entry.side} {entry.market.question[:50]} @ {price:.4f} size={size:.2f} USDC")

            attempted += 1
            resp = self.polymarket.place_limit_order(
                token_id=token_id, price=price, size=contracts, side="BUY"
            )
            if resp:
                placed += 1
                _log("    ↳ ✅ placed")
            else:
                _log("    ↳ failed")

        # 6. Mark positions
        position_values, total_pos_val = self._mark_positions(open_positions)

        result = RunResult(
            scored=scored,
            attempted_orders=attempted,
            placed_orders=placed,
            attempted_exits=0,
            placed_exits=0,
            effective_bankroll_usd=effective_bankroll,
            bankroll_source=bankroll_source,
            total_position_value=total_pos_val,
            position_values=position_values,
            evaluation_logs=logs,
        )

        self._update_performance_tracking(result)
        return result

    # ── Helper Methods ─────────────────────────────────────────────────────

    def _score_market(self, market: Market, grok_fair: FairOdds, verbose: bool = False) -> ScoredMarket | None:
        """Multi-signal fusion: Grok + News Sentiment + Sharp Wallet consensus"""
        s = self.settings

        # 1. Grok signal
        baseline = 0.5
        liq_weight = min(market.liquidity / 5000, 1.0)
        grok_p = (
            getattr(s, "fair_value_grok_weight", 0.75) * grok_fair.fair_yes_probability +
            (1 - getattr(s, "fair_value_grok_weight", 0.75)) * baseline * liq_weight
        )

        # 2. News Sentiment
        news_p = self.grok.get_news_sentiment(market) if getattr(s, "use_news_sentiment", True) else 0.5

        # 3. Sharp Wallet (placeholder)
        sharp_p = self._get_sharp_consensus(market) if getattr(s, "use_sharp_signal", True) else 0.5

        # Max-Entropy Fusion
        signals = [grok_p, news_p, sharp_p]
        fused_p = max_entropy_fusion(signals)

        grok_fair.fair_yes_probability = fused_p

        # Info Theory calculations
        p = fused_p
        market_q = [market.yes_price, market.no_price]
        your_p = [p, 1 - p]

        edge_bits = kl_divergence(your_p, market_q)
        market_entropy = entropy(market_q)

        cid = market.condition_id
        self.entropy_histories.setdefault(cid, []).append(market_entropy)
        if len(self.entropy_histories[cid]) > getattr(s, "entropy_history_length", 20):
            self.entropy_histories[cid].pop(0)

        if getattr(s, "use_info_theory", True):
            if edge_bits < getattr(s, "min_edge_bits", 0.08):
                return None
        else:
            yes_edge = p - market.yes_price
            no_edge = (1 - p) - market.no_price
            if max(yes_edge, no_edge) < getattr(s, "min_edge", 0.03):
                return None

        yes_edge = p - market.yes_price
        no_edge = (1 - p) - market.no_price
        side = "YES" if yes_edge >= no_edge else "NO"
        edge = max(yes_edge, no_edge)

        return ScoredMarket(
            market=market,
            fair_odds=grok_fair,
            side=side,
            edge=edge,
            divergence=edge,
            edge_bits=edge_bits,
            entropy=market_entropy,
            fused_prob=fused_p,
        )

    def _kelly_size(self, entry: ScoredMarket, bankroll: float) -> float:
        """More aggressive for backtesting"""
        s = self.settings
        p = getattr(entry, 'fused_prob', entry.fair_odds.fair_yes_probability)
        c = entry.market.yes_price if entry.side == "YES" else entry.market.no_price

        if c <= 0.01 or c >= 0.99:
            c = 0.50

        try:
            if entry.side == "YES":
                kelly_f = (p - c) / (1 - c)
            else:
                kelly_f = ((1 - p) - (1 - c)) / c

            kelly_f = max(0.0, min(kelly_f, 0.6))   # allow bigger bets in sim
            kelly_f *= getattr(s, "kelly_multiplier", 0.35)   # increased

            raw_size = bankroll * kelly_f
            return max(raw_size, 8.0)   # minimum $8 bet in backtest
        except:
            return 8.0
    def _rank(self, scored: list[ScoredMarket]) -> list[ScoredMarket]:
        band = getattr(self.settings, "divergence_band_size", 0.05)

        def sort_key(e: ScoredMarket):
            return (-getattr(e, 'edge_bits', 0), -e.market.liquidity, -e.market.volume, -e.divergence)

        return sorted(scored, key=sort_key)

    def _get_sharp_consensus(self, market: Market) -> float:
        """TODO: Replace with real leaderboard data later"""
        return 0.5

    def _get_current_bid(self, token_id: str) -> float:
        mid = self.polymarket.get_order_book_midpoint(token_id)
        return mid or 0.0

    def _mark_positions(self, positions: list[dict]) -> tuple[list[PositionValue], float]:
        pvals: list[PositionValue] = []
        total = 0.0
        for pos in positions:
            token_id = pos.get("asset") or pos.get("token_id") or ""
            size = float(pos.get("size") or pos.get("amount") or 0)
            avg_price = float(pos.get("avgPrice") or pos.get("avg_price") or 0.5)
            question = pos.get("question") or pos.get("title") or token_id[:30]

            bid = self._get_current_bid(token_id)
            mark = size * bid
            total += mark

            pvals.append(PositionValue(
                condition_id=pos.get("conditionId") or "",
                question=question,
                side=pos.get("outcome", "YES").upper(),
                size=size,
                avg_price=avg_price,
                current_bid=bid,
                mark_value=mark,
            ))
        return pvals, total

    def _update_performance_tracking(self, result: RunResult) -> None:
        current_equity = result.effective_bankroll_usd + result.total_position_value
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "equity": round(current_equity, 2),
            "bankroll": round(result.effective_bankroll_usd, 2),
            "position_value": round(result.total_position_value, 2),
            "placed_orders": result.placed_orders,
            "scored": len(result.scored),
            "drawdown_pct": round((self.peak_equity - current_equity) / self.peak_equity * 100, 2) 
                           if self.peak_equity > 0 else 0,
        }

        log_file = self.performance_log_path
        history = []
        if log_file.exists():
            try:
                history = json.loads(log_file.read_text())
            except:
                pass
        history.append(entry)
        log_file.write_text(json.dumps(history, indent=2))

        logger.info(f"📊 Equity ${current_equity:.2f} | DD {entry['drawdown_pct']}%")
