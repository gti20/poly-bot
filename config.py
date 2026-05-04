"""Settings loader from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Settings:
    """All configuration for the trader."""

    # === Required fields (no defaults) ===
    polymarket_private_key: str = field(default="")
    grok_api_key: str = field(default="")

    # === Optional fields with defaults ===
    polymarket_funder: Optional[str] = None
    polymarket_chain_id: int = 137  # Polygon
    polymarket_host: str = "https://clob.polymarket.com"

    grok_model: str = "grok-3-mini"
    grok_base_url: str = "https://api.x.ai/v1"

    # Risk & Trading
    bankroll_usd: float = 100.0
    use_live_balance: bool = False
    bankroll_cap_usd: float = 500.0
    kelly_multiplier: float = 0.25
    kelly_fraction_cap: float = 0.10
    min_edge: float = 0.03
    divergence_band_size: float = 0.05
    max_bet_to_liquidity_ratio: float = 0.10
    max_markets: int = 50
    market_pool_size: int = 200
    max_positions_per_run: int = 5
    max_exits_per_run: int = 10
    enable_exits: bool = True
    exit_edge_threshold: float = -0.02
    dry_run: bool = True


def load_settings() -> Settings:
    """Load settings from .env file."""
    load_dotenv()  # Loads .env if present

    return Settings(
        polymarket_private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        grok_api_key=os.getenv("GROK_API_KEY", ""),
        polymarket_funder=os.getenv("POLYMARKET_FUNDER") or None,
        grok_model=os.getenv("GROK_MODEL", "grok-3-mini"),
        bankroll_usd=float(os.getenv("TRADER_BANKROLL_USD", 100.0)),
        use_live_balance=os.getenv("TRADER_USE_LIVE_BALANCE", "false").lower() == "true",
        bankroll_cap_usd=float(os.getenv("TRADER_BANKROLL_CAP_USD", 500.0)),
        kelly_multiplier=float(os.getenv("TRADER_KELLY_MULTIPLIER", 0.25)),
        kelly_fraction_cap=float(os.getenv("TRADER_KELLY_FRACTION_CAP", 0.10)),
        min_edge=float(os.getenv("TRADER_MIN_EDGE", 0.03)),
        divergence_band_size=float(os.getenv("TRADER_DIVERGENCE_BAND_SIZE", 0.05)),
        max_bet_to_liquidity_ratio=float(os.getenv("TRADER_MAX_BET_TO_LIQUIDITY_RATIO", 0.10)),
        max_markets=int(os.getenv("TRADER_MAX_MARKETS", 50)),
        market_pool_size=int(os.getenv("TRADER_MARKET_POOL_SIZE", 200)),
        max_positions_per_run=int(os.getenv("TRADER_MAX_POSITIONS_PER_RUN", 5)),
        max_exits_per_run=int(os.getenv("TRADER_MAX_EXITS_PER_RUN", 10)),
        enable_exits=os.getenv("TRADER_ENABLE_EXITS", "true").lower() == "true",
        exit_edge_threshold=float(os.getenv("TRADER_EXIT_EDGE_THRESHOLD", -0.02)),
        dry_run=os.getenv("TRADER_DRY_RUN", "true").lower() == "true",
    )