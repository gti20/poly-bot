"""Shared dataclasses for the Polymarket Grok Trader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

   
@dataclass
class Market:
    """Represents a Polymarket binary market."""
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    liquidity: float
    volume: float
    end_date_iso: str = ""
    description: str = ""


@dataclass
class FairOdds:
    """Grok's estimated fair odds + rationale."""
    fair_yes_probability: float
    rationale: str = ""


@dataclass
class ScoredMarket:
    """Market with computed edge and side."""
    market: Market
    fair_odds: FairOdds
    side: str  # "YES" or "NO"
    edge: float
    divergence: float
    edge_bits: float = 0.0
    entropy: float = 0.0
    fused_prob: float = 0.0

@dataclass
class PositionValue:
    """Mark-to-market for a held position."""
    side: str
    question: str
    size: float
    avg_price: float
    current_bid: float
    mark_value: float


@dataclass
class RunResult:
    """Result of one full strategy run."""
    scored: List[ScoredMarket]
    attempted_orders: int
    placed_orders: int
    attempted_exits: int
    placed_exits: int
    effective_bankroll_usd: float
    bankroll_source: str
    total_position_value: float
    position_values: List[PositionValue]
    evaluation_logs: List[str]


@dataclass
class StatusResult:
    """Portfolio snapshot."""
    available_balance_usd: float
    total_position_value: float
    position_values: List[PositionValue]


@dataclass
class CloseAllResult:
    """Result of close-all operation."""
    attempted_exits: int
    placed_exits: int
    skipped_tickers: List[str]
