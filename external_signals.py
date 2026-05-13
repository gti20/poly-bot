"""
External Signals - Sharp/Whale Consensus + Order Book Signals (Async)
"""
import httpx
import json
import logging
from typing import Optional
from models import Market

logging.getLogger("httpx").setLevel(logging.WARNING)

class ExternalSignals:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_connections=20))
        self.data_api_base = "https://data-api.polymarket.com"
        self.gamma_base = "https://gamma-api.polymarket.com"
        self.clob_base = "https://clob.polymarket.com"

    async def get_sharp_consensus(self, market: Market) -> float:
        """Return a smart external probability bias (0.25-0.75) based on multiple signals."""
        if not market or not getattr(market, 'condition_id', None):
            return 0.50

        signals = []
        weights = []

        # 1. Top Holders Imbalance (Best proxy for "sharp money")
        holders_bias = await self._get_top_holders_bias(market)
        if holders_bias is not None:
            signals.append(holders_bias)
            weights.append(0.45)

        # 2. Recent Trades Momentum
        trades_bias = await self._get_recent_trades_bias(market)
        if trades_bias is not None:
            signals.append(trades_bias)
            weights.append(0.30)

        # 3. Order Book Imbalance (via CLOB)
        ob_bias = await self._get_orderbook_imbalance(market)
        if ob_bias is not None:
            signals.append(ob_bias)
            weights.append(0.25)

        if not signals:
            return 0.52

        # Weighted average
        final_bias = sum(s * w for s, w in zip(signals, weights)) / sum(weights)
        final_bias = max(0.28, min(0.72, final_bias))  # reasonable bounds

        print(f"🔪 External Signals | {market.question[:60]}... | "
              f"Holders={holders_bias:.3f} | Trades={trades_bias:.3f if trades_bias else 'N/A'} | "
              f"OB={ob_bias:.3f if ob_bias else 'N/A'} → Final {final_bias:.3f}")

        return final_bias

    async def _get_top_holders_bias(self, market: Market) -> Optional[float]:
        """Use top holders to detect smart money concentration."""
        try:
            url = f"{self.data_api_base}/v1/holders"
            params = {"market": market.condition_id, "limit": 20}
            resp = await self.client.get(url, params=params)
            if resp.status_code != 200:
                return None

            data = resp.json()
            # data structure is usually list of {token_id, holders: [...]}
            yes_holders = []
            no_holders = []

            for item in data:
                token = item.get("token", {})
                token_id = token.get("token_id") or item.get("tokenId")
                holders = item.get("holders", [])

                if any(t in str(token_id).lower() for t in ["yes", "1"]) or "yes" in str(item).lower():
                    yes_holders = holders
                else:
                    no_holders = holders

            def concentration_score(hlist):
                if not hlist:
                    return 0.0
                total = sum(float(h.get("amount", 0)) for h in hlist[:10])
                if total == 0:
                    return 0.5
                top_conc = sum(float(h.get("amount", 0)) for h in hlist[:3]) / total
                return 0.5 + (top_conc - 0.4) * 0.8  # reward concentration

            yes_score = concentration_score(yes_holders)
            no_score = concentration_score(no_holders)

            if yes_score > no_score + 0.15:
                return 0.65
            elif no_score > yes_score + 0.15:
                return 0.35
            return 0.50 + (yes_score - no_score) * 0.4

        except Exception as e:
            logging.debug(f"Holders bias failed: {e}")
            return None

    async def _get_recent_trades_bias(self, market: Market) -> Optional[float]:
        """Recent large trades momentum."""
        try:
            url = f"{self.data_api_base}/v1/trades"
            params = {"conditionId": market.condition_id, "limit": 50}
            resp = await self.client.get(url, params=params)
            if resp.status_code != 200:
                return None

            trades = resp.json()
            if not trades or len(trades) < 5:
                return None

            yes_vol = 0.0
            no_vol = 0.0
            recent_yes = 0
            recent_no = 0

            for t in trades[:30]:  # last ~30 trades
                size = float(t.get("size", 0))
                side = t.get("side", "").upper()  # "BUY" usually means buying YES or NO?
                outcome = t.get("outcome", "").upper()

                if "YES" in outcome or side == "BUY" and "yes" in str(t).lower():
                    yes_vol += size
                    recent_yes += 1
                else:
                    no_vol += size
                    recent_no += 1

            total_vol = yes_vol + no_vol
            if total_vol < 10_000:
                return None

            bias = yes_vol / total_vol
            # Slight momentum tilt
            if recent_yes > recent_no * 1.8:
                bias = min(0.68, bias + 0.08)
            elif recent_no > recent_yes * 1.8:
                bias = max(0.32, bias - 0.08)

            return bias

        except Exception as e:
            logging.debug(f"Trades bias failed: {e}")
            return None

    async def _get_orderbook_imbalance(self, market: Market) -> Optional[float]:
        """Simple bid/ask depth imbalance from CLOB."""
        try:
            # Need YES and NO token IDs
            yes_token = getattr(market, 'yes_token_id', None)
            if not yes_token:
                return None

            url = f"{self.clob_base}/book"
            resp = await self.client.get(url, params={"token_id": yes_token})
            if resp.status_code != 200:
                return None

            book = resp.json()
            bids = book.get("bids", [])  # people willing to buy YES
            asks = book.get("asks", [])  # people willing to sell YES

            bid_depth = sum(float(b.get("size", 0)) for b in bids[:8])
            ask_depth = sum(float(a.get("size", 0)) for a in asks[:8])

            if bid_depth + ask_depth < 5000:
                return None

            imbalance = bid_depth / (bid_depth + ask_depth + 1e-6)
            # Convert to probability tilt
            return 0.45 + imbalance * 0.2

        except Exception:
            return None

    async def close(self):
        await self.client.aclose()
