"""Grok API Client for fair odds and news sentiment"""
import logging
from typing import Optional

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
        """Get Grok's probability estimate for a market."""
        prompt = (
            f"Market question: {market.question}\n"
            f"Description: {market.description or 'No additional description.'}\n\n"
            "You are an expert prediction market trader. "
            "What is the true probability (0.0 to 1.0) that the YES outcome resolves True? "
            "Respond with ONLY a single float number between 0 and 1."
        )

        try:
            response = self.client.chat.completions.create(
                model=self.settings.grok_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0.0,
            )
            prob_text = response.choices[0].message.content.strip()
            prob = float(prob_text)
            prob = max(0.01, min(0.99, prob))

            return FairOdds(fair_yes_probability=prob)
        except Exception as e:
            logger.warning(f"Grok estimate failed: {e}")
            return None

    def get_news_sentiment(self, market: Market) -> float:
        """Grok-powered news sentiment probability for the market."""
        prompt = (
            f"Market: {market.question}\n"
            f"Description: {market.description or 'No extra info'}\n\n"
            f"Based ONLY on the latest public news and sentiment (ignore the current market price), "
            f"what is your estimated probability that the YES outcome is correct? "
            f"Respond with a single float between 0.0 and 1.0 only."
        )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.grok_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.0,
            )
            prob = float(response.choices[0].message.content.strip())
            return max(0.01, min(0.99, prob))
        except Exception as e:
            logger.warning(f"News sentiment failed: {e}")
            return 0.5
