import logging
from typing import Optional

from config import Settings
from models import Market

logger = logging.getLogger(__name__)


class ExternalSignals:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_external_forecast(self, market: Market) -> float:
        """Placeholder for real external API calls. Returns 0.5 until you plug in real data."""
        # TODO: Add real calls to Metaculus API, Manifold, PredictIt aggregates, etc.
        # For now we return a conservative average (you can replace with real fetch)
        logger.debug(f"External forecast requested for {market.question[:60]} — using placeholder")
        return 0.5  # replace with real logic when you add API keys

    def get_sharp_consensus(self, market: Market) -> float:
        """Sharp wallet / on-chain signal placeholder (Dune/Flipside integration later)."""
        return 0.5
