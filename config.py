"""Configuration loaded from environment variables."""

import argparse
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Polling
    poll_interval_min: int = 8
    poll_interval_max: int = 15
    request_delay_min: float = 8.0
    request_delay_max: float = 20.0

    # Deal detection
    discount_threshold: float = 0.75  # price < ref * threshold → deal
    min_price: float = 3.0            # ignore listings below this (scam prevention)
    max_price: float = 500.0          # ignore listings above this (bundle prevention)

    # Database
    db_path: str = "vinted_pokemon.db"
    db_cleanup_days: int = 30         # purge non-deal records older than this

    # Logging
    log_file: str = "bot.log"
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3

    # Runtime flags (set via CLI args, not .env)
    dry_run: bool = False
    run_once: bool = False

    # Exclusions
    excluded_sellers: list[str] = field(default_factory=list)

    # Search terms (overridable via SEARCH_TERMS env var, comma-separated)
    search_terms: list[str] = field(default_factory=lambda: [
        "pokemon",
        "pokémon",
        "nintendo ds pokemon",
        "gameboy pokemon",
        "gameboy advance pokemon",
        "pokemon ds",
        "pokemon gba",
        "juego pokemon",
    ])

    # Vinted domain
    vinted_domain: str = "www.vinted.es"


def _validate(cfg: Config) -> None:
    if cfg.poll_interval_min > cfg.poll_interval_max:
        raise ValueError(
            f"POLL_INTERVAL_MIN ({cfg.poll_interval_min}) > "
            f"POLL_INTERVAL_MAX ({cfg.poll_interval_max})"
        )
    if cfg.request_delay_min > cfg.request_delay_max:
        raise ValueError("REQUEST_DELAY_MIN > REQUEST_DELAY_MAX")
    if not (0.0 < cfg.discount_threshold < 1.0):
        raise ValueError(
            f"DISCOUNT_THRESHOLD must be between 0 and 1 (got {cfg.discount_threshold})"
        )
    if cfg.min_price < 0:
        raise ValueError("MIN_PRICE must be >= 0")
    if cfg.min_price >= cfg.max_price:
        raise ValueError("MIN_PRICE must be less than MAX_PRICE")


def load_config(
    dry_run: Optional[bool] = None,
    run_once: Optional[bool] = None,
) -> Config:
    # Import here so module-level import of config.py has no side effects
    from dotenv import load_dotenv
    load_dotenv()

    excluded_raw = os.getenv("EXCLUDED_SELLERS", "")
    excluded = [s.strip().lower() for s in excluded_raw.split(",") if s.strip()]

    search_raw = os.getenv("SEARCH_TERMS", "")
    search_override = [s.strip() for s in search_raw.split(",") if s.strip()]

    cfg = Config(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        poll_interval_min=int(os.getenv("POLL_INTERVAL_MIN", "8")),
        poll_interval_max=int(os.getenv("POLL_INTERVAL_MAX", "15")),
        request_delay_min=float(os.getenv("REQUEST_DELAY_MIN", "8.0")),
        request_delay_max=float(os.getenv("REQUEST_DELAY_MAX", "20.0")),
        discount_threshold=float(os.getenv("DISCOUNT_THRESHOLD", "0.75")),
        min_price=float(os.getenv("MIN_PRICE", "3.0")),
        max_price=float(os.getenv("MAX_PRICE", "500.0")),
        db_path=os.getenv("DB_PATH", "vinted_pokemon.db"),
        db_cleanup_days=int(os.getenv("DB_CLEANUP_DAYS", "30")),
        log_file=os.getenv("LOG_FILE", "bot.log"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        vinted_domain=os.getenv("VINTED_DOMAIN", "www.vinted.es"),
        excluded_sellers=excluded,
    )

    if search_override:
        cfg.search_terms = search_override
    if dry_run is not None:
        cfg.dry_run = dry_run
    if run_once is not None:
        cfg.run_once = run_once

    _validate(cfg)
    return cfg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vinted Pokemon Deal Alert Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py                  Run continuously\n"
            "  python main.py --dry-run        Test without sending notifications\n"
            "  python main.py --once           Single cycle then exit\n"
            "  python main.py --stats          Show DB statistics and exit\n"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without sending Telegram notifications",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single scrape cycle then exit",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print database statistics and exit",
    )
    return parser.parse_args()
