"""Telegram notification sender."""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from analyzer import Deal

logger = logging.getLogger(__name__)

# Characters that must be escaped in Telegram MarkdownV2
_MD2_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _esc(text: str) -> str:
    """Escape a string for safe embedding in a MarkdownV2 message."""
    return _MD2_RE.sub(r"\\\1", str(text))


def _time_ago(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "Hace un momento"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(tz=timezone.utc) - dt).total_seconds()))
        if seconds < 90:
            return "Hace un momento"
        if seconds < 3600:
            m = seconds // 60
            return f"Hace {m} min"
        if seconds < 86400:
            h = seconds // 3600
            return f"Hace {h}h"
        d = seconds // 86400
        return f"Hace {d}d"
    except Exception:
        return "Hace un momento"


def _condition_emoji(condition: str) -> str:
    norm = condition.lower()
    if "nuevo con" in norm:
        return "🆕"
    if "nuevo" in norm:
        return "✨"
    if "muy buen" in norm:
        return "⭐"
    if "buen" in norm:
        return "👍"
    if "satisfac" in norm:
        return "👌"
    return "📦"


def _urgency(discount_pct: float) -> str:
    if discount_pct >= 65:
        return "🔥🔥🔥"
    if discount_pct >= 50:
        return "🔥🔥"
    if discount_pct >= 35:
        return "🔥"
    return "⚡"


def _format_deal(deal: Deal) -> str:
    """Build a MarkdownV2-safe deal message. All dynamic content is escaped."""
    l = deal.listing
    lines: list[str] = []

    # Header
    lines.append(f"🎮 *GANGA DETECTADA* {_urgency(deal.discount_pct)}")
    lines.append("")

    # Title + condition
    cond_emoji = _condition_emoji(l.condition)
    cond_part = f" \\| {_esc(l.condition)}" if l.condition else ""
    lines.append(f"{cond_emoji} *{_esc(l.title)}*{cond_part}")

    # Price line
    price_str = _esc(f"{l.price:.0f}")
    ref_str = _esc(f"{deal.ref_price:.0f}")
    disc_str = _esc(str(int(deal.discount_pct)))
    lines.append(f"💰 *{price_str}€* \\(ref: {ref_str}€\\) → *\\-{disc_str}%*")

    # Seller
    seller_part = _esc(l.seller)
    if l.seller_rating:
        seller_part += f" \\({_esc(l.seller_rating)}\\)"
    lines.append(f"👤 {seller_part}")

    # Location (optional)
    if l.location:
        lines.append(f"📍 {_esc(l.location)}")

    # Time
    lines.append(f"⏰ {_esc(_time_ago(l.published_at))}")

    return "\n".join(lines)


def _inline_keyboard(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "🛒 Ver en Vinted", "url": url}]]}


# Telegram caption limit for sendPhoto
_CAPTION_LIMIT = 1024


def _truncate(text: str, limit: int = _CAPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit - 6].rfind("\n")
    return (text[:cut] if cut > 0 else text[:limit - 6]) + "\n\\.\\.\\."


class TelegramNotifier:
    _BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str, dry_run: bool = False) -> None:
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "TelegramNotifier":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    def _url(self, method: str) -> str:
        return self._BASE.format(token=self.token, method=method)

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_deal(self, deal: Deal) -> bool:
        text = _format_deal(deal)
        if self.dry_run:
            logger.info("[DRY RUN] Deal message:\n%s", text)
            return True
        if not self._ready():
            return False
        keyboard = _inline_keyboard(deal.listing.url)
        if deal.listing.image_url:
            ok = await self._send_photo(deal.listing.image_url, _truncate(text), keyboard)
            if ok:
                return True
            logger.debug("Photo send failed, falling back to text")
        return await self._send_text(text, keyboard)

    async def send_startup(self, summary: Optional[str] = None) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] Bot started. Summary: %s", summary)
            return
        if not self._ready():
            return
        body = "🤖 *Bot de gangas Pokémon activo*\nMonitorizando Vinted\\.\\.\\."
        if summary:
            body += f"\n\n_{_esc(summary)}_"
        await self._send_text(body)

    async def send_error(self, message: str) -> None:
        if self.dry_run or not self._ready():
            return
        await self._send_text(f"⚠️ *Error del bot*\n{_esc(message)}")

    async def send_daily_summary(self, stats: dict) -> None:
        if self.dry_run:
            logger.info("[DRY RUN] Daily summary: %s", stats)
            return
        if not self._ready():
            return
        text = (
            f"📊 *Resumen del día*\n\n"
            f"🎮 Gangas hoy: *{_esc(str(stats.get('deals_today', 0)))}*\n"
            f"🏆 Gangas totales: {_esc(str(stats.get('total_deals', 0)))}\n"
            f"🔍 Listings vistos: {_esc(str(stats.get('total_seen', 0)))}"
        )
        await self._send_text(text)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ready(self) -> bool:
        if not self.token or not self.chat_id:
            logger.error("Telegram token or chat_id not configured")
            return False
        return True

    async def _send_photo(
        self, photo_url: str, caption: str, keyboard: Optional[dict] = None
    ) -> bool:
        assert self._client is not None
        payload: dict = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "MarkdownV2",
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        try:
            resp = await self._client.post(self._url("sendPhoto"), json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.debug("sendPhoto error: %s", data.get("description"))
                return False
            return True
        except Exception as exc:
            logger.debug("sendPhoto exception: %s", exc)
            return False

    async def _send_text(self, text: str, keyboard: Optional[dict] = None) -> bool:
        assert self._client is not None
        payload: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        try:
            resp = await self._client.post(self._url("sendMessage"), json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description"))
                return False
            logger.info("Telegram message sent (%.60s...)", text.replace("\n", " "))
            return True
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False
