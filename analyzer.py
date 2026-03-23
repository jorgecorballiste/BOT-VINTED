"""Deal detection: matches listings against reference prices."""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from database import Listing

logger = logging.getLogger(__name__)


@dataclass
class Deal:
    listing: Listing
    ref_price: float
    matched_key: str
    discount_pct: float  # e.g. 57.0 for 57% off


def _normalize(text: str) -> str:
    """Lowercase, remove accents, collapse whitespace."""
    text = text.lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u",
        "ä": "a", "ë": "e", "ï": "i", "ö": "o", "ü": "u",
        "ñ": "n",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class DealAnalyzer:
    def __init__(self, prices_path: str = "prices.json", threshold: float = 0.75) -> None:
        self.threshold = threshold
        self._ref_prices: dict[str, float] = {}
        self._normalized_keys: dict[str, str] = {}  # normalized → original key
        self._load_prices(prices_path)

    def _load_prices(self, path: str) -> None:
        prices_file = Path(path)
        if not prices_file.exists():
            logger.error("prices.json not found at %s", path)
            return
        try:
            with prices_file.open(encoding="utf-8") as f:
                raw: dict = json.load(f)
            self._ref_prices = {k.lower(): float(v) for k, v in raw.items()}
            self._normalized_keys = {_normalize(k): k for k in self._ref_prices}
            logger.info("Loaded %d reference prices", len(self._ref_prices))
        except Exception as exc:
            logger.error("Failed to load prices.json: %s", exc)

    def _find_ref_price(self, title: str) -> Optional[tuple[str, float]]:
        """Return (matched_key, ref_price) if title matches any known game."""
        norm_title = _normalize(title)

        # Direct substring match on normalized keys
        for norm_key, orig_key in self._normalized_keys.items():
            # Build keyword list from normalized key (e.g. ["pokemon", "platino"])
            keywords = norm_key.split()
            if all(kw in norm_title for kw in keywords):
                return orig_key, self._ref_prices[orig_key]

        return None

    def analyze(
        self,
        listing: Listing,
        excluded_sellers: Optional[list[str]] = None,
    ) -> Optional[Deal]:
        """Return a Deal if listing is a bargain, otherwise None."""
        if excluded_sellers:
            if listing.seller.lower() in [s.lower() for s in excluded_sellers]:
                logger.debug("Skipping excluded seller: %s", listing.seller)
                return None

        match = self._find_ref_price(listing.title)
        if match is None:
            return None

        matched_key, ref_price = match

        if listing.price >= ref_price * self.threshold:
            return None

        discount_pct = round((1 - listing.price / ref_price) * 100, 1)
        logger.info(
            "DEAL: '%s' at %.2f€ (ref %.2f€, -%s%%)",
            listing.title,
            listing.price,
            ref_price,
            discount_pct,
        )
        return Deal(
            listing=listing,
            ref_price=ref_price,
            matched_key=matched_key,
            discount_pct=discount_pct,
        )

    def analyze_all(
        self,
        listings: list[Listing],
        excluded_sellers: Optional[list[str]] = None,
    ) -> list[Deal]:
        deals: list[Deal] = []
        for listing in listings:
            deal = self.analyze(listing, excluded_sellers)
            if deal:
                deals.append(deal)
        return deals
