"""Telegram notification sender."""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from analyzer import Deal

logger = logging.getLogger(__name__)


def _time_ago(iso_ts: Optional[str]) -> str:
    """Convert ISO timestamp to human-readable 'X minutes ago'."""
    if not iso_ts:
        return "Hace un momento"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"Hace {seconds} segundos"
        elif seconds < 3600:
            mins = seconds // 60
            return f"Hace {mins} minuto{'s' if mins != 1 else ''}"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"Hace {hours} hora{'s' if hours != 1 else ''}"
        else:
            days = seconds // 86400
            return f"Hace {days} dia{'s' if days != 1 else ''}"
    except Exception:
        return "Hace un momento"


def _format_message(deal: Deal) -> str:
    listing = deal.listing
    time_str = _time_ago(listing.published_at)
    condition_str = f" - {listing.condition}" if listing.condition else ""

    return (
        f"🎮 *GANGA DETECTADA*\n\n"
        f"📦 {listing.title}{condition_str}\n"
        f"💰 {listing.price:.0f}€ \\(precio ref: {deal.ref_price:.0f}€\\) → "
        f"*\\-{deal.discount_pct:.0f}%*\n"
        f"👤 Vendedor: {listing.seller}\n"
        f"🔗 [Ver en Vinted]({listing.url})\n"
        f"⏰ {time_str}"
    )


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str, dry_run: bool = False) -> None:
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "TelegramNotifier":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_deal(self, deal: Deal) -> bool:
        message = _format_message(deal)

        if self.dry_run:
            logger.info("[DRY RUN] Would send Telegram message:\n%s", message)
            return True

        if not self.token or not self.chat_id:
            logger.error("Telegram token or chat_id not configured")
            return False

        return await self._send_message(message, deal.listing.image_url)

    async def _send_message(self, text: str, image_url: str = "") -> bool:
        assert self._client is not None

        # Try to send photo with caption first, fallback to text
        if image_url:
            success = await self._send_photo(image_url, text)
            if success:
                return True
            logger.debug("Photo send failed, falling back to text message")

        return await self._send_text(text)

    async def _send_photo(self, photo_url: str, caption: str) -> bool:
        assert self._client is not None
        url = self.BASE_URL.format(token=self.token, method="sendPhoto")
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "MarkdownV2",
        }
        try:
            resp = await self._client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.debug("sendPhoto failed: %s", data.get("description"))
                return False
            return True
        except Exception as exc:
            logger.debug("sendPhoto exception: %s", exc)
            return False

    async def _send_text(self, text: str) -> bool:
        assert self._client is not None
        url = self.BASE_URL.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        }
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description"))
                return False
            logger.info("Notification sent for: %s", text[:60])
            return True
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    async def send_startup_message(self) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] Bot started")
            return
        if not self.token or not self.chat_id:
            return
        await self._send_text(
            "🤖 *Bot de gangas Pokémon iniciado*\n"
            "Monitorizando Vinted\\.\\.\\."
        )
