"""Core trading strategy — mirrors kalshi_grok_trader but for Polymarket."""
from __future__ import annotations

import logging
import math
from typing import Callable

from .config import Settings
from .grok_client import GrokClient
from .models import (
    CloseAllResult,
    FairOdds,
    Market,
    PositionValue,
    RunResult,
    ScoredMarket,
    StatusResult,
)
from .polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.polymarket = PolymarketClient(settings)
        self.grok = GrokClient(settings)

    # ── main entry points ───────────────────────────────────────────────────

    def run_once(
        self,
        top_n: int = 10,
        stream_progress: bool = False,
        progress_callback: Callable[[str], None] | None = None,
        verbose: bool = False,
    ) -> RunResult:
        """Run one full strategy cycle: score → rank → order → exit check."""
        s = self.settings
        logs: list[str] = []

        def _log(msg: str) -> None:
            logs.append(msg)
            if stream_progress:
                print(msg)
            if progress_callback:
                progress_callback(msg)

        # ── 1. Determine bankroll ───────────────────────────────────────────
        bankroll_source = "config"
        effective_bankroll = s.bankroll_usd

        if s.use_live_balance:
            live = self.polymarket.get_usdc_balance()
            if live is None:
                _log("ERROR: live balance fetch failed — aborting (fail-closed)")
                return RunResult(
                    scored=[], attempted_orders=0, placed_orders=0,
                    attempted_exits=0, placed_exits=0,
                    effective_bankroll_usd=0.0, bankroll_source="failed",
                    total_position_value=0.0, position_values=[],
                    evaluation_logs=logs,
                )
            effective_bankroll = min(live, s.bankroll_cap_usd)
            bankroll_source = "live"
            _log(f"Live USDC balance: ${live:.2f} → capped bankroll: ${effective_bankroll:.2f}")

        _log(f"Bankroll: ${effective_bankroll:.2f} (source={bankroll_source})")

        # ── 2. Fetch existing orders/positions (duplicate-entry guard) ──────
        open_orders = self.polymarket.get_open_orders()
        open_positions = self.polymarket.get_open_positions()
        existing_condition_ids: set[str] = set()
        for pos in open_positions:
            cid = pos.get("conditionId") or pos.get("condition_id") or ""
            if cid:
                existing_condition_ids.add(cid)
        for ord_ in open_orders:
            cid = ord_.get("conditionId") or ord_.get("condition_id") or ""
            if cid:
                existing_condition_ids.add(cid)

        _log(f"Existing positions/orders: {len(existing_condition_ids)}")

        # ── 3. Fetch and filter markets ─────────────────────────────────────
        _log(f"Fetching up to {s.market_pool_size} active markets …")
        raw_markets = self.polymarket.get_active_markets(limit=s.market_pool_size)
        _log(f"Fetched {len(raw_markets)} markets")

        # Filter out markets we already hold or have orders on
        candidate_markets = [
            m for m in raw_markets
            if m.condition_id not in existing_condition_ids and m.liquidity > 0
        ]
        _log(f"Candidates after dedup/liquidity filter: {len(candidate_markets)}")

        # ── 4. Score markets via Grok ───────────────────────────────────────
        scored: list[ScoredMarket] = []
        for i, market in enumerate(candidate_markets[: s.max_markets]):
            _log(f"  [{i+1}/{min(s.max_markets, len(candidate_markets))}] Scoring: {market.question[:70]}")
            fair = self.grok.estimate_fair_odds(market)
            if fair is None:
                _log(f"    ↳ skip — Grok returned no estimate")
                continue

            entry = self._score_market(market, fair, verbose=verbose)
            if entry is None:
                _log(f"    ↳ skip — edge below threshold ({fair.fair_yes_probability:.3f} fair vs {market.yes_price:.3f}/{market.no_price:.3f} market)")
                continue

            _log(
                f"    ↳ {entry.side} edge={entry.edge:.4f} div={entry.divergence:.4f} "
                f"liq=${market.liquidity:.0f}"
            )
            if verbose:
                _log(f"    ↳ rationale: {fair.rationale}")
            scored.append(entry)

        # ── 5. Rank ─────────────────────────────────────────────────────────
        scored = self._rank(scored)
        _log(f"Scored and ranked {len(scored)} opportunities")

        # ── 6. Place entry orders ───────────────────────────────────────────
        attempted_orders = 0
        placed_orders = 0

        for entry in scored[:top_n]:
            if placed_orders >= s.max_positions_per_run:
                break

            size = self._kelly_size(entry, effective_bankroll)
            if size < 1.0:
                _log(f"  skip {entry.market.question[:50]}: size ${size:.2f} < $1 min")
                continue

            token_id = (
                entry.market.yes_token_id if entry.side == "YES"
                else entry.market.no_token_id
            )
            price = (
                entry.market.yes_price if entry.side == "YES"
                else entry.market.no_price
            )
            contracts = size / price  # USDC → contracts at ask

            _log(
                f"  ORDER {entry.side} {entry.market.question[:50]} "
                f"@ {price:.4f} size={size:.2f} USDC ({contracts:.2f} contracts)"
            )
            attempted_orders += 1
            resp = self.polymarket.place_limit_order(
                token_id=token_id,
                price=price,
                size=contracts,
                side="BUY",
            )
            if resp:
                placed_orders += 1
                _log(f"    ↳ placed: {resp}")
            else:
                _log(f"    ↳ failed to place order")

        # ── 7. Exit management ──────────────────────────────────────────────
        attempted_exits = 0
        placed_exits = 0

        if s.enable_exits and open_positions:
            _log(f"Checking {len(open_positions)} open positions for exits …")
            for pos in open_positions[: s.max_exits_per_run]:
                result = self._maybe_exit(pos, logs=logs, verbose=verbose)
                if result is None:
                    continue
                attempted_exits += 1
                if result:
                    placed_exits += 1
                if attempted_exits >= s.max_exits_per_run:
                    break

        # ── 8. Portfolio mark ───────────────────────────────────────────────
        position_values, total_pos_val = self._mark_positions(open_positions)

        return RunResult(
            scored=scored,
            attempted_orders=attempted_orders,
            placed_orders=placed_orders,
            attempted_exits=attempted_exits,
            placed_exits=placed_exits,
            effective_bankroll_usd=effective_bankroll,
            bankroll_source=bankroll_source,
            total_position_value=total_pos_val,
            position_values=position_values,
            evaluation_logs=logs,
        )

    def get_portfolio_status(self) -> StatusResult:
        balance = self.polymarket.get_usdc_balance() or 0.0
        positions = self.polymarket.get_open_positions()
        pvals, total = self._mark_positions(positions)
        return StatusResult(
            available_balance_usd=balance,
            total_position_value=total,
            position_values=pvals,
        )

    def close_all_positions(self) -> CloseAllResult:
        positions = self.polymarket.get_open_positions()
        attempted = 0
        placed = 0
        skipped: list[str] = []

        for pos in positions:
            token_id = pos.get("asset") or pos.get("token_id") or ""
            size = float(pos.get("size") or pos.get("amount") or 0)
            bid = self._get_current_bid(token_id)
            if not token_id or size <= 0:
                skipped.append(token_id or "unknown")
                continue
            attempted += 1
            resp = self.polymarket.cancel_and_sell_position(token_id, size, bid)
            if resp:
                placed += 1
            else:
                skipped.append(token_id)

        return CloseAllResult(
            attempted_exits=attempted,
            placed_exits=placed,
            skipped_tickers=skipped,
        )

    # ── private helpers ─────────────────────────────────────────────────────

    def _score_market(
        self,
        market: Market,
        fair: FairOdds,
        verbose: bool = False,
    ) -> ScoredMarket | None:
        p = fair.fair_yes_probability
        yes_edge = p - market.yes_price
        no_edge = (1 - p) - market.no_price

        if yes_edge >= no_edge and yes_edge >= self.settings.min_edge:
            return ScoredMarket(
                market=market,
                fair_odds=fair,
                side="YES",
                edge=yes_edge,
                divergence=abs(yes_edge),
            )
        elif no_edge > yes_edge and no_edge >= self.settings.min_edge:
            return ScoredMarket(
                market=market,
                fair_odds=fair,
                side="NO",
                edge=no_edge,
                divergence=abs(no_edge),
            )
        return None

    def _kelly_size(self, entry: ScoredMarket, bankroll: float) -> float:
        """Return USDC bet size using fractional Kelly with safety caps."""
        s = self.settings
        p = entry.fair_odds.fair_yes_probability
        c = (
            entry.market.yes_price if entry.side == "YES"
            else entry.market.no_price
        )
        if c <= 0 or c >= 1:
            return 0.0

        # Kelly fraction: f* = (p - c) / (1 - c)
        kelly_f = (p - c) / (1 - c) if entry.side == "YES" else ((1 - p) - (1 - c)) / c
        kelly_f = max(0.0, kelly_f)
        kelly_f = min(kelly_f, s.kelly_fraction_cap)
        kelly_f *= s.kelly_multiplier

        raw_size = bankroll * kelly_f

        # Liquidity cap
        liq_cap = entry.market.liquidity * s.max_bet_to_liquidity_ratio
        return min(raw_size, liq_cap)

    def _rank(self, scored: list[ScoredMarket]) -> list[ScoredMarket]:
        """Rank by divergence band, then liquidity + volume desc."""
        band = self.settings.divergence_band_size

        def sort_key(e: ScoredMarket):
            band_idx = -int(e.divergence / band)  # higher divergence → lower index → first
            return (band_idx, -e.market.liquidity, -e.market.volume, -e.divergence)

        return sorted(scored, key=sort_key)

    def _maybe_exit(
        self,
        pos: dict,
        logs: list[str],
        verbose: bool = False,
    ) -> bool | None:
        """Return True if exit placed, False if not needed, None if skipped."""
        token_id = pos.get("asset") or pos.get("token_id") or ""
        size = float(pos.get("size") or pos.get("amount") or 0)
        outcome = str(pos.get("outcome") or "YES").upper()
        question = pos.get("question") or pos.get("title") or token_id[:20]
        condition_id = pos.get("conditionId") or pos.get("condition_id") or ""

        if not token_id or size <= 0:
            return None

        # Re-estimate fair odds — need to reconstruct a minimal Market
        # We'll use just the current mid price from CLOB
        current_bid = self._get_current_bid(token_id)

        # Compute held-side edge vs current bid
        # If we hold YES, edge = fair_p - bid; if NO, edge = (1-fair_p) - bid
        # Without re-querying Grok (expensive), use a simple heuristic:
        # exit if current_bid < entry_price * exit_threshold
        # For a proper implementation we re-ask Grok
        avg_price = float(pos.get("avgPrice") or pos.get("avg_price") or 0.5)

        if current_bid <= 0:
            return None

        implied_edge = current_bid - avg_price  # profit if we sell now
        if implied_edge < self.settings.exit_edge_threshold:
            logs.append(
                f"  EXIT {outcome} {question[:50]}: "
                f"bid={current_bid:.4f} avg={avg_price:.4f} "
                f"edge={implied_edge:.4f} < threshold"
            )
            resp = self.polymarket.cancel_and_sell_position(token_id, size, current_bid)
            return bool(resp)

        return False

    def _get_current_bid(self, token_id: str) -> float:
        mid = self.polymarket.get_order_book_midpoint(token_id)
        return mid or 0.0

    def _mark_positions(
        self, positions: list[dict]
    ) -> tuple[list[PositionValue], float]:
        pvals: list[PositionValue] = []
        total = 0.0
        for pos in positions:
            token_id = pos.get("asset") or pos.get("token_id") or ""
            size = float(pos.get("size") or pos.get("amount") or 0)
            avg_price = float(pos.get("avgPrice") or pos.get("avg_price") or 0)
            outcome = str(pos.get("outcome") or "YES").upper()
            question = pos.get("question") or pos.get("title") or token_id[:30]
            condition_id = pos.get("conditionId") or pos.get("condition_id") or ""

            bid = self._get_current_bid(token_id)
            mark = size * bid
            total += mark

            pvals.append(
                PositionValue(
                    condition_id=condition_id,
                    question=question,
                    side=outcome,
                    size=size,
                    avg_price=avg_price,
                    current_bid=bid,
                    mark_value=mark,
                )
            )
        return pvals, total
