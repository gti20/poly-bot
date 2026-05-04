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
    """Read/write access to Polymarket via the CLOB and Gamma APIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clob_client: Any = None  # lazy-initialised only when trading
        self._session = requests.Session()

    # ── public helpers ──────────────────────────────────────────────────────

    def get_active_markets(self, limit: int = 200) -> list[Market]:
        """Fetch active binary markets from Gamma API + enrich with CLOB prices."""
        markets: list[Market] = []
        next_cursor: str | None = None
        fetched = 0

        while fetched < limit:
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "archived": "false",
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
            if not raw_markets:
                break

            for raw in raw_markets:
                m = self._parse_gamma_market(raw)
                if m is not None:
                    markets.append(m)

            fetched += len(raw_markets)
            next_cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not next_cursor:
                break

        return markets[:limit]

    def get_order_book_midpoint(self, token_id: str) -> float | None:
        """Return the midpoint price (0–1) for a given token ID, or None."""
        try:
            resp = self._session.get(
                f"{CLOB_HOST}/midpoint",
                params={"token_id": token_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            mid = data.get("mid")
            if mid is not None:
                return float(mid)
        except Exception as exc:
            logger.debug("Midpoint fetch failed for %s: %s", token_id, exc)
        return None

    def get_usdc_balance(self) -> float | None:
        """Fetch live USDC balance. Returns None on failure (fails closed)."""
        client = self._get_clob_client()
        if client is None:
            return None
        try:
            balance_data = client.get_balance_allowance(
                params={"asset_type": "COLLATERAL"}
            )
            raw = (
                balance_data.get("balance")
                or balance_data.get("allowance")
                or 0
            )
            # Balance is in 10^6 units (USDC has 6 decimals on Polygon)
            return float(raw) / 1e6
        except Exception as exc:
            logger.warning("Balance fetch failed: %s", exc)
            return None

    def get_open_positions(self) -> list[dict]:
        """Return raw open positions from the CLOB."""
        client = self._get_clob_client()
        if client is None:
            return []
        try:
            # Try common method names for positions
            for method_name in ["get_positions", "get_user_positions", "positions"]:
                if hasattr(client, method_name):
                    result = getattr(client, method_name)()
                    return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("Positions fetch failed: %s", exc)
        return []

    def get_open_orders(self) -> list[dict]:
        """Return raw open orders."""
        client = self._get_clob_client()
        if client is None:
            return []
        try:
            # Try common method names for orders
            for method_name in ["get_orders", "get_user_orders", "orders"]:
                if hasattr(client, method_name):
                    result = getattr(client, method_name)()
                    return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("Orders fetch failed: %s", exc)
        return []

    
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,  # "BUY" or "SELL"
    ) -> dict | None:
        """Place a GTC limit order — matching the proven copy-trading bot pattern."""
        if self.settings.dry_run:
            logger.info(
                "[DRY-RUN] Would place %s %.4f @ %.4f (token=%s)",
                side, size, price, token_id,
            )
            return {"dry_run": True, "side": side, "size": size, "price": price}

        client = self._get_clob_client()
        if client is None:
            logger.error("Cannot place order: CLOB client not initialised")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL

            clob_side = BUY if side.upper() == "BUY" else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 2),
                side=clob_side,
            )

            # This is the reliable call used in working bots
            resp = client.create_and_post_order(order_args=order_args)

            logger.info(f"✅ Order placed: {resp}")
            return resp

        except Exception as exc:
            logger.error(f"Order placement failed: {exc}")
            return None


    def cancel_and_sell_position(
        self,
        token_id: str,
        size: float,
        current_bid: float,
    ) -> dict | None:
        """Market-sell an existing position at current bid."""
        if self.settings.dry_run:
            logger.info(
                "[DRY-RUN] Would sell %.4f of token %s @ %.4f bid",
                size, token_id, current_bid,
            )
            return {"dry_run": True}

        return self.place_limit_order(
            token_id=token_id,
            price=current_bid,
            size=size,
            side="SELL",
        )
    
    def cancel_and_sell_position(
        self,
        token_id: str,
        size: float,
        current_bid: float,
    ) -> dict | None:
        """Market-sell an existing position at current bid."""
        if self.settings.dry_run:
            logger.info(
                "[DRY-RUN] Would sell %.4f of token %s @ %.4f bid",
                size, token_id, current_bid,
            )
            return {"dry_run": True}

        return self.place_limit_order(
            token_id=token_id,
            price=current_bid,
            size=size,
            side="SELL",
        )
    def auth_diagnostics(self) -> dict:
        """Return basic auth diagnostic info."""
        client = self._get_clob_client()
        if client is None:
            return {"error": "Could not initialise CLOB client — check credentials"}
        try:
            ok = client.get_ok()
            server_time = client.get_server_time()
            return {"ok": ok, "server_time": server_time, "address": client.get_address()}
        except Exception as exc:
            return {"error": str(exc)}

    # ── private ─────────────────────────────────────────────────────────────

    def _get_clob_client(self) -> Any | None:
        if self._clob_client is not None:
            return self._clob_client

        pk = self.settings.polymarket_private_key
        if not pk:
            logger.warning("POLYMARKET_PRIVATE_KEY not set; read-only mode")
            return None

        try:
            from py_clob_client.client import ClobClient

            client = ClobClient(
                host=CLOB_HOST,
                key=pk,
                chain_id=self.settings.polymarket_chain_id,
                signature_type=0,   # Change to 1 if using Magic/Email wallet
                funder=self.settings.polymarket_funder or None,
            )

            client.set_api_creds(client.create_or_derive_api_creds())

            self._clob_client = client
            logger.info("✅ CLOB client initialized")
            return client

        except Exception as exc:
            logger.error(f"CLOB client init failed: {exc}")
            return None

    def _parse_gamma_market(self, raw: dict) -> Market | None:
        """Parse a Gamma API market dict into our Market model."""
        try:
            tokens = raw.get("tokens") or raw.get("clobTokenIds") or []
            if len(tokens) < 2:
                return None

            # Gamma returns tokens as list of {outcome, token_id} dicts
            # or as plain list of token_id strings
            if isinstance(tokens[0], dict):
                yes_token = next(
                    (t["token_id"] for t in tokens if str(t.get("outcome", "")).upper() == "YES"),
                    tokens[0]["token_id"],
                )
                no_token = next(
                    (t["token_id"] for t in tokens if str(t.get("outcome", "")).upper() == "NO"),
                    tokens[1]["token_id"],
                )
            else:
                yes_token, no_token = str(tokens[0]), str(tokens[1])

            # Best prices — Gamma often includes outcomePrices
            outcome_prices = raw.get("outcomePrices") or []
            if len(outcome_prices) >= 2:
                try:
                    yes_price = float(outcome_prices[0])
                    no_price = float(outcome_prices[1])
                except (TypeError, ValueError):
                    yes_price = 0.5
                    no_price = 0.5
            else:
                yes_price = 0.5
                no_price = 0.5

            liquidity = float(raw.get("liquidity") or raw.get("liquidityNum") or 0)
            volume = float(raw.get("volume") or raw.get("volumeNum") or 0)

            question = (
                raw.get("question")
                or raw.get("title")
                or raw.get("description")
                or ""
            )
            condition_id = raw.get("conditionId") or raw.get("id") or ""
            end_date = raw.get("endDate") or raw.get("endDateIso") or ""
            description = raw.get("description") or ""

            if not condition_id or not question:
                return None

            return Market(
                condition_id=str(condition_id),
                question=str(question),
                yes_token_id=str(yes_token),
                no_token_id=str(no_token),
                yes_price=yes_price,
                no_price=no_price,
                liquidity=liquidity,
                volume=volume,
                end_date_iso=str(end_date),
                description=str(description),
            )
        except Exception as exc:
            logger.debug("Failed to parse market: %s | %s", exc, raw.get("question", "?"))
            return None
