"""Command-line interface for the Polymarket Grok Trader."""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict

from .config import load_settings
from .trader import Trader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Polymarket Grok Trader — AI-powered prediction market bot"
    )
    p.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Number of top opportunities to consider for ordering (default: 10)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print Grok rationale and detailed skip reasons",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Print current portfolio status and exit",
    )
    p.add_argument(
        "--close-all",
        action="store_true",
        dest="close_all",
        help="Close all open positions and exit",
    )
    p.add_argument(
        "--auth-check",
        action="store_true",
        dest="auth_check",
        help="Run auth diagnostics and exit",
    )
    p.add_argument(
        "--list-grok-models",
        action="store_true",
        dest="list_grok_models",
        help="List available Grok models and exit",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    settings = load_settings()

    if settings.dry_run:
        logger.info("*** DRY-RUN MODE — no live orders will be placed ***")
    else:
        logger.warning("*** LIVE TRADING ENABLED ***")

    trader = Trader(settings)

    # ── one-shot commands ───────────────────────────────────────────────────
    if args.auth_check:
        diag = trader.polymarket.auth_diagnostics()
        print("\nAuth diagnostics:")
        for k, v in diag.items():
            print(f"  {k}: {v}")
        return

    if args.list_grok_models:
        models = trader.grok.list_models()
        print("\nAvailable Grok models:")
        for m in models:
            print(f"  {m}")
        return

    if args.status:
        status = trader.get_portfolio_status()
        print(f"\nAvailable USDC: ${status.available_balance_usd:.2f}")
        print(f"Position value: ${status.total_position_value:.2f}")
        print(f"Total equity:   ${status.available_balance_usd + status.total_position_value:.2f}")
        if status.position_values:
            print("\nOpen positions:")
            for pv in status.position_values:
                print(
                    f"  {pv.side:3s}  {pv.question[:55]:<55s}"
                    f"  size={pv.size:.2f}  avg={pv.avg_price:.4f}"
                    f"  bid={pv.current_bid:.4f}  mark=${pv.mark_value:.2f}"
                )
        return

    if args.close_all:
        result = trader.close_all_positions()
        print(f"\nClose-all: attempted={result.attempted_exits} placed={result.placed_exits}")
        if result.skipped_tickers:
            print(f"Skipped: {result.skipped_tickers}")
        return

    # ── main strategy run ───────────────────────────────────────────────────
    result = trader.run_once(
        top_n=args.top,
        stream_progress=True,
        verbose=args.verbose,
    )

    print("\n" + "=" * 70)
    print(f"Run summary")
    print("=" * 70)
    print(f"  Candidates scored:  {len(result.scored)}")
    print(f"  Attempted orders:   {result.attempted_orders}")
    print(f"  Placed orders:      {result.placed_orders}")
    print(f"  Attempted exits:    {result.attempted_exits}")
    print(f"  Placed exits:       {result.placed_exits}")
    print(f"  Bankroll:           ${result.effective_bankroll_usd:.2f} ({result.bankroll_source})")
    print(f"  Position value:     ${result.total_position_value:.2f}")

    if result.scored:
        print(f"\nTop {args.top} opportunities:")
        for i, e in enumerate(result.scored[:args.top], 1):
            print(
                f"  {i:2d}. [{e.side}] {e.market.question[:60]}"
                f"\n       edge={e.edge:.4f}  div={e.divergence:.4f}"
                f"  liq=${e.market.liquidity:.0f}  fair={e.fair_odds.fair_yes_probability:.4f}"
                f"  yes_px={e.market.yes_price:.4f}  no_px={e.market.no_price:.4f}"
            )
            if args.verbose:
                print(f"       rationale: {e.fair_odds.rationale}")

    if result.position_values:
        print("\nPositions (mark-to-bid):")
        for pv in result.position_values:
            print(
                f"  {pv.side:3s}  {pv.question[:55]:<55s}  mark=${pv.mark_value:.2f}"
            )


if __name__ == "__main__":
    main()
