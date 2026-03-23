"""Main polling loop for the Vinted Pokemon deal alert bot."""

import asyncio
import logging
import logging.handlers
import random
import signal
import sys
from pathlib import Path

from analyzer import DealAnalyzer
from config import Config, load_config, parse_args
from database import Database
from notifier import TelegramNotifier
from scraper import VintedScraper


def setup_logging(config: Config) -> None:
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_file,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.analyzer = DealAnalyzer(threshold=config.discount_threshold)
        self._running = True

    def _stop(self, *_) -> None:
        logger.info("Shutdown signal received, stopping...")
        self._running = False

    async def _run_cycle(self) -> int:
        """Run one scrape + analyze + notify cycle. Returns number of deals sent."""
        deals_sent = 0

        async with VintedScraper(self.config) as scraper:
            # Warm up session with homepage visit
            await scraper._get_auth_token()
            await asyncio.sleep(random.uniform(2.0, 4.0))

            listings = await scraper.search_all_terms()

        if not listings:
            logger.warning("No listings fetched in this cycle")
            return 0

        # Filter already-seen
        new_listings = [l for l in listings if not self.db.is_seen(l.id)]
        logger.info(
            "%d new listings (out of %d total)", len(new_listings), len(listings)
        )

        deals = self.analyzer.analyze_all(
            new_listings, excluded_sellers=self.config.excluded_sellers
        )

        async with TelegramNotifier(
            token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            dry_run=self.config.dry_run,
        ) as notifier:
            for deal in deals:
                sent = await notifier.send_deal(deal)
                # Mark as seen regardless of notification success to avoid spam
                self.db.mark_seen(
                    deal.listing,
                    is_deal=True,
                    ref_price=deal.ref_price,
                    discount_pct=deal.discount_pct,
                )
                if sent:
                    deals_sent += 1
                await asyncio.sleep(random.uniform(1.0, 3.0))

        # Mark non-deal new listings as seen too
        deal_ids = {d.listing.id for d in deals}
        for listing in new_listings:
            if listing.id not in deal_ids:
                self.db.mark_seen(listing, is_deal=False)

        stats = self.db.stats()
        logger.info(
            "Cycle complete. Deals sent: %d | DB stats: %s", deals_sent, stats
        )
        return deals_sent

    async def run(self) -> None:
        # Install signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop)

        logger.info(
            "Bot started. Dry-run: %s | Threshold: %.0f%% | Interval: %d-%d min",
            self.config.dry_run,
            self.config.discount_threshold * 100,
            self.config.poll_interval_min,
            self.config.poll_interval_max,
        )

        async with TelegramNotifier(
            token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            dry_run=self.config.dry_run,
        ) as notifier:
            await notifier.send_startup_message()

        cycle = 0
        while self._running:
            cycle += 1
            logger.info("--- Starting cycle #%d ---", cycle)
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unexpected error in cycle #%d: %s", cycle, exc, exc_info=True)

            if not self._running:
                break

            interval = random.uniform(
                self.config.poll_interval_min * 60,
                self.config.poll_interval_max * 60,
            )
            logger.info(
                "Sleeping %.1f minutes until next cycle...", interval / 60
            )
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        logger.info("Bot stopped cleanly.")


async def main() -> None:
    args = parse_args()
    config = load_config(dry_run=args.dry_run)
    setup_logging(config)

    if config.dry_run:
        logger.info("DRY-RUN mode enabled — no notifications will be sent")

    if not config.telegram_bot_token and not config.dry_run:
        logger.warning(
            "TELEGRAM_BOT_TOKEN not set. "
            "Set it in .env or use --dry-run for testing."
        )

    bot = Bot(config)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
