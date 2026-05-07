"""Grok API Client — POWERHOUSE: rich CoT, JSON, ensemble"""
import logging
import json
from typing import Optional, List
from datetime import datetime

from openai import OpenAI

from config import Settings
from models import FairOdds, Market

logger = logging.getLogger(__name__)


class GrokClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.grok_api_key,
            base_url=settings.grok_base_url,
        )

    def estimate_fair_odds(self, market: Market) -> Optional[FairOdds]:
        """Rich CoT + structured JSON + 3-run ensemble."""
        system_prompt = (
            "You are a world-class +EV prediction market trader. "
            "Think step-by-step: base rates → news → resolution rules → crowd biases. "
            "Output ONLY valid JSON: "
            '{"fair_yes_probability": 0.XX, "confidence": 0-100, "reasoning": "one sentence"}'
        )

        user_prompt = (
            f"Date: {datetime.now().isoformat()}\n"
            f"Market: {market.question}\n"
            f"Description: {market.description or 'No description.'}\n"
            f"Closes: {getattr(market, 'end_date_iso', 'unknown')}\n"
            f"Current YES price: {getattr(market, 'yes_price', 'N/A')}\n"
            f"Volume: ${getattr(market, 'volume', 'N/A')} | Liquidity: ${getattr(market, 'liquidity', 'N/A')}\n\n"
            "What is the TRUE P(YES resolves True)?"
        )

        probs: List[float] = []
        for i in range(3):  # ensemble
            try:
                resp = self.client.chat.completions.create(
                    model=self.settings.grok_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=self.settings.grok_temperature,
                    max_tokens=self.settings.grok_max_tokens,
                )
                data = json.loads(resp.choices[0].message.content.strip())
                p = float(data["fair_yes_probability"])
                probs.append(max(0.01, min(0.99, p)))
            except Exception as e:
                logger.warning(f"Grok run {i+1} failed: {e}")

        if not probs:
            return None

        fair_p = sum(probs) / len(probs)
        return FairOdds(
            fair_yes_probability=fair_p,
            rationale="Ensemble of 3 Grok CoT runs"
        )

    def get_news_sentiment(self, market: Market) -> float:
        """Lightweight news sentiment fallback."""
        # (same as original or enhanced prompt — keep your existing logic if preferred)
        prompt = f"Market: {market.question}\nBased on latest news only, P(YES)? Single float 0-1."
        try:
            resp = self.client.chat.completions.create(
                model=self.settings.grok_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0.2,
            )
            return max(0.01, min(0.99, float(resp.choices[0].message.content.strip())))
        except Exception:
            return 0.5
