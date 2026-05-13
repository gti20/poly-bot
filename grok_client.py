"""Grok API Client — POWERHOUSE: rich CoT, JSON, ensemble"""
import logging
import json
import asyncio
from typing import Optional
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
        """Improved news sentiment with better prompt and fallback"""
        prompt = f"""
Market: {market.question}
Description: {market.description or 'No description available.'}

Based on recent news, public sentiment, and expert analysis (ignore current market odds), 
what is the probability that the YES outcome happens? 

Answer with ONLY a number between 0.0 and 1.0. Do not explain.
"""

        try:
            response = self.client.chat.completions.create(
                model=self.settings.grok_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.0,
            )
            text = response.choices[0].message.content.strip()
            prob = float(text)
            return max(0.05, min(0.95, prob))
        except Exception as e:
            print(f"    ⚠️ News sentiment failed: {e} → using 0.5")
            return 0.5
    async def _fetch_single_prediction(self, market: Market, system_prompt: str) -> Optional[float]:
        """Helper for parallel LLM calls."""
        # Note: We REMOVE the market.yes_price from the prompt to avoid anchoring bias
        user_prompt = (
            f"Market: {market.question}\n"
            f"Description: {market.description or 'No description.'}\n"
            f"Closes: {getattr(market, 'end_date_iso', 'unknown')}\n"
        )
        try:
            # Use run_in_executor if the openai client is synchronous
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self.client.chat.completions.create(
                model=self.settings.grok_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            ))
            data = json.loads(resp.choices[0].message.content.strip())
            return float(data["fair_yes_probability"])
        except Exception as e:
            logger.warning(f"Grok call failed: {e}")
            return None

    async def estimate_fair_odds(self, market: Market) -> Optional[FairOdds]:
        """Run 3-run ensemble in PARALLEL."""
        system_prompt = (
            "You are a world-class +EV prediction market trader. "
            "Think step-by-step. Output ONLY JSON: "
            '{"fair_yes_probability": 0.XX, "reasoning": "..."}'
        )
        
        # Parallel execution to solve the latency bottleneck
        tasks = [self._fetch_single_prediction(market, system_prompt) for _ in range(3)]
        results = await asyncio.gather(*tasks)
        
        valid_probs = [p for p in results if p is not None]
        if not valid_probs:
            return None

        return FairOdds(
            fair_yes_probability=sum(valid_probs) / len(valid_probs),
            rationale=f"Ensemble of {len(valid_probs)} parallel Grok runs"
        )
