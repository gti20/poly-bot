"""Settings loader from environment variables."""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Optional, Dict

from dotenv import load_dotenv


@dataclass
class Settings:
    """All configuration for the trader — POWERHOUSE EDITION."""
    # Required
    polymarket_private_key: str = field(default="")
    grok_api_key: str = field(default="")

    # Polymarket
    polymarket_funder: Optional[str] = None
    polymarket_chain_id: int = 137
    polymarket_host: str = "https://clob.polymarket.com"

    # Grok
    grok_model: str = "grok-3-mini"
    grok_base_url: str = "https://api.x.ai/v1"
    grok_temperature: float = 0.1
    grok_max_tokens: int = 400

    # Risk & Portfolio
    bankroll_usd: float = 1000.0
    use_live_balance: bool = False
    bankroll_cap_usd: float = 10000.0
    kelly_multiplier: float = 0.25
    kelly_fraction_cap: float = 0.10
    portfolio_kelly: bool = True
    max_drawdown_daily: float = 0.05
    max_drawdown_monthly: float = 0.15
    category_caps: Dict[str, float] = field(default_factory=lambda: {
        "politics": 0.25, "crypto": 0.15, "sports": 0.10, "other": 0.05
    })

    # Signals & Filters
    use_news_sentiment: bool = True
    use_sharp_signal: bool = True
    use_external_forecasts: bool = True
    fair_value_grok_weight: float = 0.60
    min_edge: float = 0.04
    min_edge_bits: float = 0.10
    min_liquidity_usd: float = 10000.0
    min_volume_usd: float = 50000.0
    trader_price_improvement: float = 0.005

    # Trading
    max_markets: int = 50
    market_pool_size: int = 200
    max_positions_per_run: int = 5
    max_exits_per_run: int = 10
    enable_exits: bool = True
    exit_edge_threshold: float = -0.02
    dry_run: bool = True

    # Info Theory
    use_info_theory: bool = True
    entropy_history_length: int = 20
    insider_sigma: float = 3.0
    divergence_band_size: float = 0.05
    max_bet_to_liquidity_ratio: float = 0.10


def load_settings() -> Settings:
    """Load settings from .env file."""
    load_dotenv()

    try:
        caps_raw = os.getenv("TRADER_CATEGORY_CAPS", '{"politics":0.25,"crypto":0.15,"sports":0.10,"other":0.05}')
        category_caps = json.loads(caps_raw)
    except Exception:
        category_caps = {"politics":0.25,"crypto":0.15,"sports":0.10,"other":0.05}

    return Settings(
        polymarket_private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        grok_api_key=os.getenv("GROK_API_KEY", ""),
        polymarket_funder=os.getenv("POLYMARKET_FUNDER") or None,
        grok_model=os.getenv("GROK_MODEL", "grok-3-mini"),
        grok_temperature=float(os.getenv("GROK_TEMPERATURE", 0.1)),
        grok_max_tokens=int(os.getenv("GROK_MAX_TOKENS", 400)),
        bankroll_usd=float(os.getenv("TRADER_BANKROLL_USD", 1000.0)),
        use_live_balance=os.getenv("TRADER_USE_LIVE_BALANCE", "false").lower() == "true",
        bankroll_cap_usd=float(os.getenv("TRADER_BANKROLL_CAP_USD", 10000.0)),
        kelly_multiplier=float(os.getenv("TRADER_KELLY_MULTIPLIER", 0.25)),
        portfolio_kelly=os.getenv("TRADER_PORTFOLIO_KELLY", "true").lower() == "true",
        max_drawdown_daily=float(os.getenv("TRADER_MAX_DRAWDOWN_DAILY", 0.05)),
        max_drawdown_monthly=float(os.getenv("TRADER_MAX_DRAWDOWN_MONTHLY", 0.15)),
        category_caps=category_caps,
        use_news_sentiment=os.getenv("TRADER_USE_NEWS_SENTIMENT", "true").lower() == "true",
        use_sharp_signal=os.getenv("TRADER_USE_SHARP_SIGNAL", "true").lower() == "true",
        use_external_forecasts=os.getenv("TRADER_USE_EXTERNAL_FORECASTS", "true").lower() == "true",
        fair_value_grok_weight=float(os.getenv("TRADER_FAIR_VALUE_GROK_WEIGHT", 0.60)),
        min_edge=float(os.getenv("TRADER_MIN_EDGE", 0.04)),
        min_edge_bits=float(os.getenv("TRADER_MIN_EDGE_BITS", 0.10)),
        min_liquidity_usd=float(os.getenv("TRADER_MIN_LIQUIDITY_USD", 10000.0)),
        min_volume_usd=float(os.getenv("TRADER_MIN_VOLUME_USD", 50000.0)),
        trader_price_improvement=float(os.getenv("TRADER_PRICE_IMPROVEMENT", 0.005)),
        max_markets=int(os.getenv("TRADER_MAX_MARKETS", 50)),
        market_pool_size=int(os.getenv("TRADER_MARKET_POOL_SIZE", 200)),
        max_positions_per_run=int(os.getenv("TRADER_MAX_POSITIONS_PER_RUN", 5)),
        enable_exits=os.getenv("TRADER_ENABLE_EXITS", "true").lower() == "true",
        exit_edge_threshold=float(os.getenv("TRADER_EXIT_EDGE_THRESHOLD", -0.02)),
        dry_run=os.getenv("TRADER_DRY_RUN", "true").lower() == "true",
        use_info_theory=os.getenv("TRADER_USE_INFO_THEORY", "true").lower() == "true",
        entropy_history_length=int(os.getenv("TRADER_ENTROPY_HISTORY_LENGTH", 20)),
        insider_sigma=float(os.getenv("TRADER_INSIDER_SIGMA", 3.0)),
        divergence_band_size=float(os.getenv("TRADER_DIVERGENCE_BAND_SIZE", 0.05)),
        max_bet_to_liquidity_ratio=float(os.getenv("TRADER_MAX_BET_TO_LIQUIDITY_RATIO", 0.10)),
    )
