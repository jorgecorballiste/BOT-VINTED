"""Deal detection: matches listings against reference prices."""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from database import Listing

logger = logging.getLogger(__name__)

# Titles containing these words are likely lots/bundles — skip for single-game pricing
LOT_KEYWORDS: frozenset[str] = frozenset([
    "lote", "lotes", "pack", "bundle", "coleccion", "coleccion",
    "varios", "lot", "conjunto", "surtido",
])

# Vinted condition strings → multiplier applied to the ref_price threshold.
# Better condition = higher effective price, so we stay strict.
# Worse condition = lower effective value, so we require a larger absolute discount.
_CONDITION_MULTIPLIERS: list[tuple[str, float]] = [
    ("nuevo con etiquetas", 1.10),
    ("nuevo sin etiquetas", 1.05),
    ("muy buen estado",     1.00),
    ("buen estado",         0.95),
    ("satisfactorio",       0.85),
]


def _normalize(text: str) -> str:
    """Lowercase + NFKD accent removal + collapse punctuation to spaces."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_set(text: str) -> frozenset[str]:
    return frozenset(_normalize(text).split())


def _condition_multiplier(condition: str) -> float:
    norm = _normalize(condition)
    for key, mult in _CONDITION_MULTIPLIERS:
        if key in norm:
            return mult
    return 1.0


@dataclass
class Deal:
    listing: Listing
    ref_price: float
    matched_key: str
    discount_pct: float  # e.g. 57.3 for 57.3 % off
    effective_threshold: float = field(repr=False, default=0.75)


class DealAnalyzer:
    def __init__(
        self,
        prices_path: str = "prices.json",
        threshold: float = 0.75,
        min_price: float = 3.0,
        max_price: float = 500.0,
    ) -> None:
        self.threshold = threshold
        self.min_price = min_price
        self.max_price = max_price
        # List of (keyword_wordset, original_key, ref_price) sorted most-specific first
        self._index: list[tuple[frozenset[str], str, float]] = []
        self._load_prices(prices_path)

    def _load_prices(self, path: str) -> None:
        prices_file = Path(path)
        if not prices_file.exists():
            logger.error("prices.json not found at %s", path)
            return
        try:
            with prices_file.open(encoding="utf-8") as f:
                raw: dict = json.load(f)
            entries = [
                (_word_set(k), k, float(v))
                for k, v in raw.items()
                if not k.startswith("_")  # skip comment keys
            ]
            # More keywords = more specific → checked first to avoid short-key shadowing
            self._index = sorted(entries, key=lambda x: len(x[0]), reverse=True)
            logger.info("Loaded %d reference prices from %s", len(self._index), path)
        except Exception as exc:
            logger.error("Failed to load prices.json: %s", exc)

    def _match(self, title: str) -> Optional[tuple[str, float]]:
        """Word-set matching: every keyword must appear as a whole word in title.

        Returns (matched_key, ref_price) for the most specific match, or None.

        Why word-set instead of substring:
          - "pokemon x" keywords = {"pokemon","x"}; title "Pokemon XY" words = {"pokemon","xy"}
            → "x" ∉ {"pokemon","xy"} → NO match  ✓
          - "pokemon negro 2" checked before "pokemon negro"
            → title "Pokemon Negro 2" matches "negro 2" first  ✓
        """
        title_words = _word_set(title)
        for key_words, orig_key, price in self._index:
            if key_words.issubset(title_words):
                return orig_key, price
        return None

    def analyze(
        self,
        listing: Listing,
        excluded_sellers: Optional[list[str]] = None,
    ) -> Optional[Deal]:
        # ── Price sanity ────────────────────────────────────────────────────
        if listing.price < self.min_price:
            logger.debug("Skip %s: price %.2f < min %.2f", listing.id, listing.price, self.min_price)
            return None
        if listing.price > self.max_price:
            logger.debug("Skip %s: price %.2f > max %.2f", listing.id, listing.price, self.max_price)
            return None

        # ── Seller exclusion ────────────────────────────────────────────────
        if excluded_sellers and listing.seller.lower() in excluded_sellers:
            logger.debug("Skip excluded seller: %s", listing.seller)
            return None

        # ── Lot / bundle detection ──────────────────────────────────────────
        title_words = _word_set(listing.title)
        if title_words & LOT_KEYWORDS:
            logger.debug("Skip lot/bundle: '%s'", listing.title)
            return None

        # ── Reference price match ───────────────────────────────────────────
        match = self._match(listing.title)
        if match is None:
            return None
        matched_key, ref_price = match

        # ── Condition-adjusted threshold ────────────────────────────────────
        # New item → threshold slightly higher (already a deal vs mint price)
        # Poor condition → threshold lower (needs bigger absolute cut to be worth it)
        cond_mult = _condition_multiplier(listing.condition)
        effective_threshold = self.threshold * cond_mult

        if listing.price >= ref_price * effective_threshold:
            return None

        discount_pct = round((1.0 - listing.price / ref_price) * 100, 1)
        logger.info(
            "DEAL [-%s%%] '%s' @ %.2f€  (ref %.2f€, key='%s', cond='%s')",
            discount_pct, listing.title, listing.price,
            ref_price, matched_key, listing.condition,
        )
        return Deal(
            listing=listing,
            ref_price=ref_price,
            matched_key=matched_key,
            discount_pct=discount_pct,
            effective_threshold=effective_threshold,
        )

    def analyze_all(
        self,
        listings: list[Listing],
        excluded_sellers: Optional[list[str]] = None,
    ) -> list[Deal]:
        deals = [
            deal
            for listing in listings
            if (deal := self.analyze(listing, excluded_sellers)) is not None
        ]
        # Best deals first
        deals.sort(key=lambda d: d.discount_pct, reverse=True)
        return deals
