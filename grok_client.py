"""Grok API client — requests fair-yes-probability for prediction markets."""
from __future__ import annotations

import json
import logging

import requests

from .config import Settings
from .models import FairOdds, Market

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a prediction market analyst.
You will be given a Polymarket binary question and any available context.
Your task: estimate the TRUE probability that the YES outcome occurs,
expressed as a decimal between 0.00 and 1.00.

IMPORTANT:
- Be calibrated. Do not anchor to 0.50 unless genuinely uncertain.
- Reason step by step, then emit a JSON object as the LAST thing in your response.
- The JSON must be: {"fair_yes_probability": <float>, "rationale": "<one sentence>"}
- No markdown fences around the JSON.
"""


class GrokClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {settings.grok_api_key}",
                "Content-Type": "application/json",
            }
        )

    def estimate_fair_odds(self, market: Market) -> FairOdds | None:
        """Ask Grok for a fair YES probability for the given market."""
        user_msg = self._build_prompt(market)
        payload = {
            "model": self.settings.grok_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }

        try:
            resp = self._session.post(
                f"{self.settings.grok_base_url}/chat/completions",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return self._parse_response(content)
        except Exception as exc:
            logger.warning("Grok API error for '%s': %s", market.question[:60], exc)
            return None

    def list_models(self) -> list[str]:
        """Return available Grok model IDs."""
        try:
            resp = self._session.get(
                f"{self.settings.grok_base_url}/models",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            logger.warning("Model list failed: %s", exc)
            return []

    # ── private ─────────────────────────────────────────────────────────────

    def _build_prompt(self, market: Market) -> str:
        lines = [
            f"Question: {market.question}",
            f"Current YES price: {market.yes_price:.4f}",
            f"Current NO price:  {market.no_price:.4f}",
        ]
        if market.end_date_iso:
            lines.append(f"Resolution date: {market.end_date_iso}")
        if market.description and market.description != market.question:
            lines.append(f"Description: {market.description[:400]}")
        lines.append(
            "\nGiven this information, what is your best estimate of the "
            "true probability that YES resolves?"
        )
        return "\n".join(lines)

    def _parse_response(self, content: str) -> FairOdds | None:
        """Extract the JSON object from the end of Grok's response."""
        # Find last '{' ... '}' block
        start = content.rfind("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            logger.warning("No JSON found in Grok response: %s", content[:200])
            return None
        try:
            obj = json.loads(content[start : end + 1])
            p = float(obj["fair_yes_probability"])
            p = max(0.01, min(0.99, p))  # clamp to valid range
            rationale = str(obj.get("rationale", ""))
            return FairOdds(fair_yes_probability=p, rationale=rationale)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("JSON parse failed: %s | %s", exc, content[:200])
            return None
