"""Main polling loop for the Vinted Pokemon deal alert bot."""

import asyncio
import logging
import logging.handlers
import random
import signal
import sys

from analyzer import DealAnalyzer
from config import Config, load_config, parse_args
from database import Database
from notifier import TelegramNotifier
from scraper import VintedScraper

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_ERRORS = 5


def setup_logging(config: Config) -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        config.log_file,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


class Bot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.analyzer = DealAnalyzer(
            threshold=config.discount_threshold,
            min_price=config.min_price,
            max_price=config.max_price,
        )
        self._running = True
        self._consecutive_errors = 0

    def _stop(self, *_) -> None:
        logger.info("Shutdown signal received, stopping...")
        self._running = False

    async def _run_cycle(self) -> int:
        """Execute one scrape → analyze → notify cycle. Returns deals sent."""
        deals_sent = 0

        async with VintedScraper(self.config) as scraper:
            await scraper.warmup()
            await asyncio.sleep(random.uniform(2.0, 4.0))
            listings = await scraper.search_all_terms()

        if not listings:
            logger.warning("No listings fetched this cycle")
            return 0

        # Bulk deduplication: one DB query instead of N
        seen_ids = self.db.bulk_are_seen([l.id for l in listings])
        new_listings = [l for l in listings if l.id not in seen_ids]
        logger.info(
            "%d new / %d already seen / %d total",
            len(new_listings), len(seen_ids), len(listings),
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
                # Always mark seen to prevent re-alerting even if notification failed
                self.db.mark_seen(
                    deal.listing,
                    is_deal=True,
                    ref_price=deal.ref_price,
                    discount_pct=deal.discount_pct,
                )
                if sent:
                    deals_sent += 1
                await asyncio.sleep(random.uniform(1.0, 3.0))

        # Bulk-persist all non-deal new listings in one transaction
        deal_ids = {d.listing.id for d in deals}
        non_deals = [l for l in new_listings if l.id not in deal_ids]
        self.db.bulk_mark_seen(non_deals, is_deal=False)

        logger.info(
            "Cycle done | deals sent: %d | DB: %s",
            deals_sent, self.db.stats(),
        )
        return deals_sent

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop)

        summary = (
            f"Dominio: {self.config.vinted_domain} | "
            f"Umbral: -{int((1 - self.config.discount_threshold) * 100)}% | "
            f"Intervalo: {self.config.poll_interval_min}–{self.config.poll_interval_max} min | "
            f"Dry-run: {self.config.dry_run}"
        )
        logger.info("Bot starting | %s", summary)

        # Startup notification
        async with TelegramNotifier(
            token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            dry_run=self.config.dry_run,
        ) as notifier:
            await notifier.send_startup(summary)

        # Purge stale non-deal records on boot
        deleted = self.db.cleanup_old(self.config.db_cleanup_days)
        if deleted:
            logger.info("Cleaned %d old records from DB", deleted)

        cycle = 0
        while self._running:
            cycle += 1
            logger.info("=== Cycle #%d ===", cycle)
            try:
                await self._run_cycle()
                self._consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_errors += 1
                logger.error(
                    "Cycle #%d error (%d consecutive): %s",
                    cycle, self._consecutive_errors, exc, exc_info=True,
                )
                if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many consecutive errors — sending alert")
                    async with TelegramNotifier(
                        token=self.config.telegram_bot_token,
                        chat_id=self.config.telegram_chat_id,
                        dry_run=self.config.dry_run,
                    ) as notifier:
                        await notifier.send_error(
                            f"{self._consecutive_errors} errores consecutivos. "
                            f"Último: {type(exc).__name__}: {exc}"
                        )
                    self._consecutive_errors = 0

            if self.config.run_once or not self._running:
                break

            interval = random.uniform(
                self.config.poll_interval_min * 60,
                self.config.poll_interval_max * 60,
            )
            logger.info("Next cycle in %.1f min...", interval / 60)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        logger.info("Bot stopped.")


async def main() -> None:
    args = parse_args()

    # --stats: print DB info and exit (no logging setup needed)
    if args.stats:
        cfg = load_config()
        db = Database(cfg.db_path)
        stats = db.stats()
        top = db.top_deals(limit=10)
        print("\n=== Vinted Pokemon Bot — Statistics ===")
        print(f"  Total listings seen : {stats['total_seen']}")
        print(f"  Total deals found   : {stats['total_deals']}")
        print(f"  Deals today         : {stats['deals_today']}")
        if top:
            print("\n  Top 10 deals (all time):")
            for r in top:
                print(
                    f"    {r['discount_pct']:5.1f}% off │ "
                    f"{r['title'][:40]:<40} │ "
                    f"{r['price']:.0f}€ (ref {r['ref_price']:.0f}€)"
                )
        print()
        return

    config = load_config(dry_run=args.dry_run, run_once=args.once)
    setup_logging(config)

    if config.dry_run:
        logger.info("DRY-RUN mode — no Telegram messages will be sent")
    if config.run_once:
        logger.info("--once mode — will exit after first cycle")
    if not config.telegram_bot_token and not config.dry_run:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Use --dry-run to test.")

    await Bot(config).run()


if __name__ == "__main__":
    asyncio.run(main())
