"""Configuration loaded from environment variables."""

import argparse
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


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
    discount_threshold: float = 0.75  # price < ref * 0.75 → deal

    # Database
    db_path: str = "vinted_pokemon.db"

    # Logging
    log_file: str = "bot.log"
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    log_backup_count: int = 3

    # Runtime flags
    dry_run: bool = False

    # Excluded sellers (comma-separated in .env)
    excluded_sellers: list[str] = field(default_factory=list)

    # Search terms
    search_terms: list[str] = field(default_factory=lambda: [
        "pokemon",
        "pokémon",
        "nintendo ds pokemon",
        "gameboy pokemon",
        "gameboy advance pokemon",
        "pokemon ds",
        "pokemon gba",
        "pokemon game",
    ])

    # Vinted country domain
    vinted_domain: str = "www.vinted.es"


def load_config(dry_run: Optional[bool] = None) -> Config:
    excluded_raw = os.getenv("EXCLUDED_SELLERS", "")
    excluded = [s.strip().lower() for s in excluded_raw.split(",") if s.strip()]

    cfg = Config(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        poll_interval_min=int(os.getenv("POLL_INTERVAL_MIN", "8")),
        poll_interval_max=int(os.getenv("POLL_INTERVAL_MAX", "15")),
        request_delay_min=float(os.getenv("REQUEST_DELAY_MIN", "8.0")),
        request_delay_max=float(os.getenv("REQUEST_DELAY_MAX", "20.0")),
        discount_threshold=float(os.getenv("DISCOUNT_THRESHOLD", "0.75")),
        db_path=os.getenv("DB_PATH", "vinted_pokemon.db"),
        log_file=os.getenv("LOG_FILE", "bot.log"),
        vinted_domain=os.getenv("VINTED_DOMAIN", "www.vinted.es"),
        excluded_sellers=excluded,
    )

    if dry_run is not None:
        cfg.dry_run = dry_run

    return cfg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vinted Pokemon Deal Alert Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without sending notifications (for testing)",
    )
    return parser.parse_args()
