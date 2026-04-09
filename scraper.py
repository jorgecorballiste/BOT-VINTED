"""Vinted scraper using the public catalog API."""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import Config
from database import Listing

logger = logging.getLogger(__name__)

# ── User-Agent pool (updated 2025) ───────────────────────────────────────────
_USER_AGENTS: list[dict] = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"macOS"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "sec_ch_ua": "",  # Safari doesn't send sec-ch-ua
        "platform": '"macOS"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "sec_ch_ua": "",  # Firefox doesn't send sec-ch-ua
        "platform": "",
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "sec_ch_ua": "",
        "platform": "",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "sec_ch_ua": '"Microsoft Edge";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"Linux"',
    },
]

# Vinted numeric condition codes → human-readable label
_CONDITION_MAP = {
    "6": "Nuevo con etiquetas",
    "1": "Nuevo sin etiquetas",
    "2": "Muy buen estado",
    "3": "Buen estado",
    "4": "Satisfactorio",
}


def _build_headers(ua_entry: dict, domain: str) -> dict:
    headers = {
        "User-Agent": ua_entry["ua"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{domain}/",
        "Origin": f"https://{domain}",
        "Connection": "keep-alive",
    }
    if ua_entry["sec_ch_ua"]:
        headers["sec-ch-ua"] = ua_entry["sec_ch_ua"]
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = ua_entry["platform"]
    if "Firefox" not in ua_entry["ua"] and "Safari/605" not in ua_entry["ua"]:
        headers["sec-fetch-dest"] = "empty"
        headers["sec-fetch-mode"] = "cors"
        headers["sec-fetch-site"] = "same-origin"
        headers["DNT"] = "1"
    return headers


def _parse_condition(item: dict) -> str:
    cond = item.get("item_condition") or item.get("status") or ""
    if isinstance(cond, dict):
        return cond.get("title") or cond.get("name") or ""
    s = str(cond).strip()
    if s.isdigit():
        return _CONDITION_MAP.get(s, s)
    return s


def _parse_price(item: dict) -> tuple[float, str]:
    """Return (amount, currency_code). Raises ValueError if unparseable."""
    raw = item.get("price") or item.get("total_item_price")
    if isinstance(raw, dict):
        amount = float(raw.get("amount") or 0)
        currency = raw.get("currency_code", "EUR")
    elif raw is not None:
        amount = float(raw)
        currency = "EUR"
    else:
        raise ValueError("no price field")
    if amount <= 0:
        raise ValueError(f"price <= 0: {amount}")
    return amount, currency


def _parse_published_at(item: dict) -> Optional[str]:
    raw = item.get("first_time_published") or item.get("created_at_ts")
    if not raw:
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    return str(raw)


def _parse_item(item: dict, search_term: str, domain: str) -> Optional[Listing]:
    try:
        item_id = str(item.get("id", "")).strip()
        if not item_id or item_id == "0":
            return None

        title = (item.get("title") or "").strip()
        if not title:
            return None

        try:
            price, currency = _parse_price(item)
        except (ValueError, TypeError):
            return None

        url = (item.get("url") or "").strip()
        if not url.startswith("http"):
            url = f"https://{domain}/items/{item_id}"

        # Full-size image preferred
        photos = item.get("photos") or []
        image_url = ""
        if photos and isinstance(photos[0], dict):
            p = photos[0]
            image_url = p.get("full_size_url") or p.get("url") or p.get("thumb_url") or ""

        condition = _parse_condition(item)

        user = item.get("user") or {}
        if not isinstance(user, dict):
            user = {}
        seller = user.get("login") or user.get("username") or "desconocido"
        seller_id = str(user.get("id", ""))

        # feedback_reputation is 0–1 float on Vinted; show as percentage
        rep = user.get("feedback_reputation")
        if rep is not None:
            try:
                val = float(rep)
                seller_rating = f"{val * 100:.0f}%" if val <= 1.0 else f"{val:.1f}/5"
            except (ValueError, TypeError):
                seller_rating = str(rep)
        else:
            seller_rating = ""

        loc = item.get("country_title") or item.get("country") or ""
        location = loc.get("title", "") if isinstance(loc, dict) else str(loc)

        return Listing(
            id=item_id,
            title=title,
            price=price,
            currency=currency,
            condition=condition,
            url=url,
            image_url=image_url,
            seller=seller,
            seller_id=seller_id,
            seller_rating=seller_rating,
            location=location,
            published_at=_parse_published_at(item),
            search_term=search_term,
        )
    except Exception as exc:
        logger.debug("Parse error for item %s: %s", item.get("id"), exc, exc_info=True)
        return None


class VintedScraper:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._ua_entry: dict = random.choice(_USER_AGENTS)
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "VintedScraper":
        self._ua_entry = random.choice(_USER_AGENTS)
        self._session = httpx.AsyncClient(
            headers=_build_headers(self._ua_entry, self.config.vinted_domain),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.aclose()
            self._session = None

    async def warmup(self) -> None:
        """Visit the Vinted homepage to acquire session cookies before API calls."""
        assert self._session is not None
        try:
            resp = await self._session.get(
                f"https://{self.config.vinted_domain}/",
                headers=_build_headers(self._ua_entry, self.config.vinted_domain),
            )
            resp.raise_for_status()
            logger.debug(
                "Session warmed up (HTTP %s, cookies: %s)",
                resp.status_code,
                list(self._session.cookies.keys()),
            )
        except Exception as exc:
            logger.warning("Warmup failed: %s", exc)

    def _rotate_ua(self) -> None:
        self._ua_entry = random.choice(_USER_AGENTS)
        assert self._session is not None
        self._session.headers.update(
            _build_headers(self._ua_entry, self.config.vinted_domain)
        )

    async def search(
        self,
        term: str,
        per_page: int = 96,
        max_retries: int = 3,
    ) -> list[Listing]:
        assert self._session is not None
        url = f"https://{self.config.vinted_domain}/api/v2/catalog/items"
        params = {"search_text": term, "per_page": per_page, "order": "newest_first"}

        backoff = 2.0
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    self._rotate_ua()

                resp = await self._session.get(url, params=params)

                if resp.status_code in (401, 403):
                    logger.info(
                        "HTTP %d for '%s' (attempt %d/%d) — re-warming session",
                        resp.status_code, term, attempt, max_retries,
                    )
                    await self.warmup()
                    await asyncio.sleep(backoff + random.uniform(1.0, 3.0))
                    backoff *= 2
                    continue

                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", backoff * 2))
                    logger.warning("Rate-limited (429) for '%s' — backing off %.1fs", term, wait)
                    await asyncio.sleep(wait)
                    backoff *= 2
                    continue

                resp.raise_for_status()
                data = resp.json()
                items_raw = data.get("items", [])
                listings = [
                    listing
                    for raw in items_raw
                    if (listing := _parse_item(raw, term, self.config.vinted_domain))
                ]
                logger.info("Search '%s': %d/%d items parsed", term, len(listings), len(items_raw))
                return listings

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP %s for '%s' (attempt %d/%d)",
                    exc.response.status_code, term, attempt, max_retries,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "Network error for '%s' (attempt %d/%d): %s",
                    term, attempt, max_retries, exc,
                )
            except Exception as exc:
                logger.error("Unexpected error for '%s': %s", term, exc, exc_info=True)
                break

            if attempt < max_retries:
                jitter = random.uniform(0, backoff * 0.25)
                await asyncio.sleep(backoff + jitter)
                backoff *= 2

        return []

    async def search_all_terms(self) -> list[Listing]:
        all_listings: list[Listing] = []
        seen_ids: set[str] = set()

        for i, term in enumerate(self.config.search_terms):
            if i > 0:
                delay = random.uniform(
                    self.config.request_delay_min,
                    self.config.request_delay_max,
                )
                logger.debug("Waiting %.1fs before next term '%s'...", delay, term)
                await asyncio.sleep(delay)

            new_count = 0
            for listing in await self.search(term):
                if listing.id not in seen_ids:
                    seen_ids.add(listing.id)
                    all_listings.append(listing)
                    new_count += 1
            logger.debug("'%s': +%d unique listings", term, new_count)

        logger.info("Total unique listings this cycle: %d", len(all_listings))
        return all_listings
