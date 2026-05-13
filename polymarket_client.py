"""Thin wrapper around the Polymarket CLOB API."""
from __future__ import annotations

import logging
from typing import Any

import requests

from config import Settings
from models import Market, PositionValue

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"


class PolymarketClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clob_client: Any = None
        self._session = requests.Session()
        self.clob_host = CLOB_HOST  # ← Important fix

    def get_active_markets(self, limit: int = 200) -> list[Market]:
        """Fetch active markets from Gamma API"""
        markets: list[Market] = []
        next_cursor = None
        fetched = 0

        while fetched < limit:
            params = {
                "active": "true",
                "closed": "false",
                "limit": min(100, limit - fetched),
                "order": "volume",
                "ascending": "false",
            }
            if next_cursor:
                params["next_cursor"] = next_cursor

            try:
                resp = self._session.get(f"{GAMMA_API}/markets", params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Gamma API error: %s", exc)
                break

            raw_markets = data if isinstance(data, list) else data.get("markets", [])
            for raw in raw_markets:
                m = self._parse_gamma_market(raw)
                if m:
                    markets.append(m)

            fetched += len(raw_markets)
            next_cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not next_cursor:
                break

        return markets[:limit]

    def get_order_book_midpoint(self, token_id: str) -> float | None:
        """Return the midpoint price from CLOB (fixed)"""
        try:
            resp = self._session.get(
                f"{self.clob_host}/midpoint",
                params={"token_id": token_id},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            
            mid = data.get("mid")
            if mid is not None:
                price = float(mid)
                logger.debug(f"Midpoint for {token_id}: {price:.4f}")
                return price

        except Exception as exc:
            logger.debug(f"Midpoint fetch failed for {token_id}: {exc}")
        
        return None

    # ... keep the rest of your file (get_usdc_balance, place_limit_order, etc.)

    def _parse_gamma_market(self, raw: dict) -> Market | None:
        """Parse Gamma market (your existing method is fine)"""
        try:
            tokens = raw.get("tokens") or raw.get("clobTokenIds") or []
            if len(tokens) < 2:
                return None

            if isinstance(tokens[0], dict):
                yes_token = next((t["token_id"] for t in tokens if str(t.get("outcome", "")).upper() == "YES"), tokens[0]["token_id"])
                no_token = next((t["token_id"] for t in tokens if str(t.get("outcome", "")).upper() == "NO"), tokens[1]["token_id"])
            else:
                yes_token, no_token = str(tokens[0]), str(tokens[1])

            outcome_prices = raw.get("outcomePrices") or []
            yes_price = float(outcome_prices[0]) if len(outcome_prices) >= 2 else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) >= 2 else 0.5

            return Market(
                condition_id=str(raw.get("conditionId") or raw.get("id")),
                question=str(raw.get("question") or raw.get("title")),
                yes_token_id=str(yes_token),
                no_token_id=str(no_token),
                yes_price=yes_price,
                no_price=no_price,
                liquidity=float(raw.get("liquidity") or raw.get("liquidityNum") or 0),
                volume=float(raw.get("volume") or raw.get("volumeNum") or 0),
                end_date_iso=str(raw.get("endDate") or ""),
                description=str(raw.get("description") or ""),
            )
        except Exception as exc:
            logger.debug("Failed to parse market: %s", exc)
            return None
