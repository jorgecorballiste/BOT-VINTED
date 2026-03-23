"""Vinted scraper using the public catalog API."""

import asyncio
import logging
import random
from typing import Optional

import httpx

from config import Config
from database import Listing

logger = logging.getLogger(__name__)

# Realistic desktop User-Agent pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


def _build_headers(user_agent: str, domain: str) -> dict:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{domain}/",
        "Origin": f"https://{domain}",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "DNT": "1",
        "Connection": "keep-alive",
    }


def _parse_item(item: dict, search_term: str, domain: str) -> Optional[Listing]:
    """Parse a single Vinted API item into a Listing."""
    try:
        item_id = str(item.get("id", ""))
        if not item_id:
            return None

        title = item.get("title", "").strip()
        price_raw = item.get("price", item.get("total_item_price", {}))

        if isinstance(price_raw, dict):
            price = float(price_raw.get("amount", 0))
            currency = price_raw.get("currency_code", "EUR")
        else:
            price = float(price_raw or 0)
            currency = "EUR"

        if price <= 0:
            return None

        url = item.get("url", "")
        if not url.startswith("http"):
            url = f"https://{domain}/items/{item_id}"

        # Image
        photos = item.get("photos", [])
        image_url = ""
        if photos:
            photo = photos[0]
            image_url = (
                photo.get("url")
                or photo.get("full_size_url")
                or photo.get("thumb_url", "")
            )

        # Condition / status
        status_raw = item.get("status", item.get("item_closing_action", ""))
        if isinstance(status_raw, dict):
            condition = status_raw.get("title", "")
        else:
            condition = str(status_raw)

        # Seller
        user = item.get("user", {})
        seller = user.get("login", user.get("username", "desconocido"))

        published_at = item.get("first_time_published", item.get("created_at_ts", ""))
        if isinstance(published_at, int):
            from datetime import datetime, timezone
            published_at = datetime.fromtimestamp(published_at, tz=timezone.utc).isoformat()

        return Listing(
            id=item_id,
            title=title,
            price=price,
            currency=currency,
            condition=condition,
            url=url,
            image_url=image_url,
            seller=seller,
            published_at=str(published_at) if published_at else None,
            search_term=search_term,
        )
    except Exception as exc:
        logger.debug("Error parsing item %s: %s", item.get("id"), exc)
        return None


class VintedScraper:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._session: Optional[httpx.AsyncClient] = None
        self._current_ua = random.choice(USER_AGENTS)

    async def __aenter__(self) -> "VintedScraper":
        self._current_ua = random.choice(USER_AGENTS)
        self._session = httpx.AsyncClient(
            headers=_build_headers(self._current_ua, self.config.vinted_domain),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.aclose()
            self._session = None

    async def _get_auth_token(self) -> Optional[str]:
        """Fetch the Vinted CSRF/session token from the homepage."""
        assert self._session is not None
        try:
            resp = await self._session.get(
                f"https://{self.config.vinted_domain}/",
                headers=_build_headers(self._current_ua, self.config.vinted_domain),
            )
            resp.raise_for_status()
            # Vinted sets an _vinted_fr_session cookie automatically
            logger.debug("Homepage fetched, cookies: %s", dict(self._session.cookies))
            return None
        except Exception as exc:
            logger.warning("Could not fetch homepage: %s", exc)
            return None

    async def search(
        self,
        term: str,
        per_page: int = 96,
        max_retries: int = 3,
    ) -> list[Listing]:
        """Search Vinted for a term and return parsed listings."""
        assert self._session is not None

        url = f"https://{self.config.vinted_domain}/api/v2/catalog/items"
        params = {
            "search_text": term,
            "per_page": per_page,
            "order": "newest_first",
            "catalog_ids": "",
        }

        delay = 2.0
        for attempt in range(1, max_retries + 1):
            try:
                # Rotate UA on retry
                if attempt > 1:
                    self._current_ua = random.choice(USER_AGENTS)
                    self._session.headers.update(
                        _build_headers(self._current_ua, self.config.vinted_domain)
                    )

                resp = await self._session.get(url, params=params)

                if resp.status_code == 401:
                    logger.info("401 received, refreshing session token...")
                    await self._get_auth_token()
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                if resp.status_code == 429:
                    logger.warning("Rate limited (429), backing off %.1fs", delay * 2)
                    await asyncio.sleep(delay * 2)
                    delay *= 2
                    continue

                resp.raise_for_status()
                data = resp.json()

                items_raw = data.get("items", [])
                listings: list[Listing] = []
                for raw in items_raw:
                    listing = _parse_item(raw, term, self.config.vinted_domain)
                    if listing:
                        listings.append(listing)

                logger.info(
                    "Search '%s': %d items found, %d parsed",
                    term,
                    len(items_raw),
                    len(listings),
                )
                return listings

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP %s for term '%s' (attempt %d/%d): %s",
                    exc.response.status_code,
                    term,
                    attempt,
                    max_retries,
                    exc,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "Request error for term '%s' (attempt %d/%d): %s",
                    term,
                    attempt,
                    max_retries,
                    exc,
                )
            except Exception as exc:
                logger.error("Unexpected error for term '%s': %s", term, exc)
                break

            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2

        return []

    async def search_all_terms(self) -> list[Listing]:
        """Search all configured terms with inter-request delays."""
        all_listings: list[Listing] = []
        seen_ids: set[str] = set()

        for i, term in enumerate(self.config.search_terms):
            if i > 0:
                delay = random.uniform(
                    self.config.request_delay_min,
                    self.config.request_delay_max,
                )
                logger.debug("Waiting %.1fs before next search term...", delay)
                await asyncio.sleep(delay)

            listings = await self.search(term)
            for listing in listings:
                if listing.id not in seen_ids:
                    seen_ids.add(listing.id)
                    all_listings.append(listing)

        logger.info("Total unique listings fetched: %d", len(all_listings))
        return all_listings
